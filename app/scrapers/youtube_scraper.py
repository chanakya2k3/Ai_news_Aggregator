"""Scrape the latest videos of the channels listed in channels.toml via YouTube's RSS feeds.

Run from the project root:
    uv run python -m app.scrapers.youtube_scraper
    uv run python -m app.scrapers.youtube_scraper --hours 24
"""

import argparse
import sys
import time
import tomllib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import feedparser
import httpx

from app.scrapers.models import Channel, ChannelConfig, ChannelResult, Video
from app.scrapers.youtube_resolver import (
    DEFAULT_COOKIES,
    DEFAULT_HEADERS,
    YOUTUBE_BASE,
    ChannelResolveError,
    ChannelResolver,
)

FEED_URL = YOUTUBE_BASE + "/feeds/videos.xml"
DEFAULT_CHANNELS_FILE = Path("channels.toml")
DEFAULT_CACHE_FILE = Path(".cache/channel_ids.json")
FEED_ATTEMPTS = 4  # YouTube's feed answers 404/500 at random; retry before believing it
FEED_RETRY_DELAY = 1.0  # seconds, multiplied by the attempt number


class FeedError(Exception):
    """The channel's RSS feed could not be downloaded or read."""


def load_channel_configs(path: Path) -> list[ChannelConfig]:
    if not path.exists():
        raise FileNotFoundError(f"Channels file not found: {path}")
    with path.open("rb") as file:
        data = tomllib.load(file)
    return [ChannelConfig.model_validate(entry) for entry in data.get("channels", [])]


def parse_feed(xml: str, channel_id: str) -> tuple[str, list[Video]]:
    """Parse a channel RSS feed into (channel title, videos newest first)."""
    feed = feedparser.parse(xml)
    if feed.bozo and not feed.entries:
        raise FeedError(f"Could not parse feed for {channel_id}: {feed.bozo_exception}")

    channel_title = feed.feed.get("title", channel_id)
    videos = []
    for entry in feed.entries:
        thumbnails = entry.get("media_thumbnail") or [{}]
        views = entry.get("media_statistics", {}).get("views")
        videos.append(
            Video(
                video_id=entry.yt_videoid,
                channel_id=entry.get("yt_channelid", channel_id),
                channel_title=entry.get("author", channel_title),
                title=entry.title,
                url=entry.link,
                published_at=_parse_timestamp(entry.published),
                updated_at=_parse_timestamp(entry.updated) if entry.get("updated") else None,
                description=entry.get("summary", ""),
                thumbnail_url=thumbnails[0].get("url"),
                views=int(views) if views and views.isdigit() else None,
            )
        )
    videos.sort(key=lambda video: video.published_at, reverse=True)
    return channel_title, videos


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(UTC)


def build_client() -> httpx.Client:
    return httpx.Client(
        headers=DEFAULT_HEADERS,
        cookies=DEFAULT_COOKIES,
        follow_redirects=True,
        timeout=httpx.Timeout(20.0, connect=10.0),
        transport=httpx.HTTPTransport(retries=2),  # retries connection failures only
    )


class YouTubeScraper:
    def __init__(self, client: httpx.Client, resolver: ChannelResolver):
        self._client = client
        self._resolver = resolver

    def fetch_channel_videos(self, channel_id: str, attempts: int = FEED_ATTEMPTS) -> tuple[str, list[Video]]:
        """Fetch and parse a channel's feed.

        YouTube serves this feed unreliably: the same channel can answer 500 or
        404 one second and 200 the next, so a failing status is retried before
        we treat it as real.
        """
        last_status = None
        for attempt in range(1, attempts + 1):
            try:
                response = self._client.get(FEED_URL, params={"channel_id": channel_id})
            except httpx.HTTPError as exc:
                raise FeedError(f"Could not reach the feed for {channel_id}: {exc}") from exc

            if response.status_code == 200:
                return parse_feed(response.text, channel_id)

            last_status = response.status_code
            if attempt < attempts:
                time.sleep(FEED_RETRY_DELAY * attempt)  # 1s, 2s, 3s...

        if last_status == 404:
            raise FeedError(
                f"No feed for channel {channel_id} after {attempts} tries "
                "(channel removed, ID wrong, or YouTube is rate limiting this IP)"
            )
        raise FeedError(f"YouTube returned HTTP {last_status} for the feed of {channel_id} after {attempts} tries")

    def scrape_channel(self, config: ChannelConfig, since: datetime | None = None) -> ChannelResult:
        try:
            channel_id = self._resolver.resolve(config.url)
            title, videos = self.fetch_channel_videos(channel_id)
        except (ChannelResolveError, FeedError) as exc:
            return ChannelResult(config=config, error=str(exc))

        if since is not None:
            videos = [video for video in videos if video.published_at >= since]
        channel = Channel(
            channel_id=channel_id,
            title=title,
            url=f"{YOUTUBE_BASE}/channel/{channel_id}",
            tags=config.tags,
        )
        return ChannelResult(config=config, channel=channel, videos=videos)

    def scrape(self, configs: list[ChannelConfig], since: datetime | None = None) -> list[ChannelResult]:
        """Scrape every channel; one failing channel never stops the others."""
        return [self.scrape_channel(config, since) for config in configs]


def print_results(results: list[ChannelResult]) -> None:
    for result in results:
        if not result.ok:
            print(f"\n[ERROR] {result.config.url}\n  {result.error}")
            continue

        channel = result.channel
        tags = f"  [{', '.join(channel.tags)}]" if channel.tags else ""
        print(f"\n== {channel.title} ({channel.channel_id}){tags} - {len(result.videos)} video(s)")
        for video in result.videos:
            published = video.published_at.astimezone().strftime("%Y-%m-%d %H:%M")
            views = f"{video.views:,} views" if video.views is not None else "views n/a"
            print(f"  {published}  {video.title}")
            print(f"                    {video.url}  ({views})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Show the latest videos from the channels in channels.toml")
    parser.add_argument("--channels", type=Path, default=DEFAULT_CHANNELS_FILE, help="path to channels.toml")
    parser.add_argument("--hours", type=float, help="only show videos published in the last N hours")
    args = parser.parse_args(argv)

    # Titles often contain emoji; don't crash on consoles that can't print them.
    sys.stdout.reconfigure(errors="replace")

    try:
        configs = load_channel_configs(args.channels)
    except (FileNotFoundError, tomllib.TOMLDecodeError, ValueError) as exc:
        print(f"Could not load channels: {exc}", file=sys.stderr)
        return 2
    if not configs:
        print(f"No channels listed in {args.channels}. Add a [[channels]] entry first.")
        return 0

    since = datetime.now(UTC) - timedelta(hours=args.hours) if args.hours else None
    with build_client() as client:
        scraper = YouTubeScraper(client, ChannelResolver(client, DEFAULT_CACHE_FILE))
        results = scraper.scrape(configs, since)

    print_results(results)
    failed = sum(not result.ok for result in results)
    print(f"\nDone: {len(results) - failed} channel(s) OK, {failed} failed.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
