"""Scrape the configured channels and store anything new in the database.

    uv run python -m app.services.ingest
    uv run python -m app.services.ingest --hours 48

Safe to run as often as you like: videos already stored are skipped, so only
genuinely new ones are reported.
"""

import argparse
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.database import repository
from app.database.session import get_session
from app.scrapers.youtube_resolver import ChannelResolver
from app.scrapers.youtube_scraper import (
    DEFAULT_CACHE_FILE,
    DEFAULT_CHANNELS_FILE,
    YouTubeScraper,
    build_client,
    load_channel_configs,
)


def ingest(channels_file: Path = DEFAULT_CHANNELS_FILE, hours: float | None = None) -> dict[str, int]:
    """Scrape every configured channel and save new videos. Returns a small summary."""
    configs = load_channel_configs(channels_file)
    since = datetime.now(UTC) - timedelta(hours=hours) if hours else None

    with build_client() as client:
        scraper = YouTubeScraper(client, ChannelResolver(client, DEFAULT_CACHE_FILE))
        results = scraper.scrape(configs, since)

    summary = {"channels": 0, "failed": 0, "new_videos": 0}

    with get_session() as session:
        for result in results:
            if not result.ok:
                print(f"[ERROR] {result.config.url}: {result.error}", file=sys.stderr)
                summary["failed"] += 1
                continue

            channel_row = repository.upsert_channel(session, result.channel)
            session.flush()  # make sure channel_row.id exists before inserting videos
            new_videos = repository.add_new_videos(session, channel_row, result.videos)

            summary["channels"] += 1
            summary["new_videos"] += len(new_videos)

            print(f"{result.channel.title}: {len(new_videos)} new of {len(result.videos)} in feed")
            for video in new_videos:
                published = video.published_at.astimezone().strftime("%Y-%m-%d %H:%M")
                print(f"   + {published}  {video.title}")

    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scrape channels and store new videos")
    parser.add_argument("--channels", type=Path, default=DEFAULT_CHANNELS_FILE)
    parser.add_argument("--hours", type=float, help="only store videos published in the last N hours")
    args = parser.parse_args(argv)

    sys.stdout.reconfigure(errors="replace")
    summary = ingest(args.channels, args.hours)

    with get_session() as session:
        totals = repository.count_rows(session)

    print(
        f"\nDone: {summary['new_videos']} new video(s) from {summary['channels']} channel(s),"
        f" {summary['failed']} failed."
    )
    print(f"Database now holds {totals['channels']} channels, {totals['videos']} videos.")
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
