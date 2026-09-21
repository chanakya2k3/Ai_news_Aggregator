"""Fetch the transcript (captions) of a YouTube video.

Run from the project root:
    uv run python -m app.scrapers.transcript_scraper --channel @AndrejKarpathy
    uv run python -m app.scrapers.transcript_scraper --video EWvNQjAaOHw --timestamps
"""

import argparse
import re
import sys
from urllib.parse import parse_qs, urlparse

from youtube_transcript_api import (
    AgeRestricted,
    CouldNotRetrieveTranscript,
    IpBlocked,
    NoTranscriptFound,
    RequestBlocked,
    TranscriptsDisabled,
    VideoUnavailable,
    YouTubeTranscriptApi,
)
from youtube_transcript_api._transcripts import FetchedTranscript, Transcript, TranscriptList

from app.scrapers.models import TranscriptSegment, Video, VideoTranscript
from app.scrapers.youtube_resolver import ChannelResolver
from app.scrapers.youtube_scraper import YouTubeScraper, build_client

DEFAULT_LANGUAGES = ("en",)
VIDEO_ID_RE = re.compile(r"^[\w-]{11}$")


class TranscriptUnavailable(Exception):
    """No transcript could be fetched for this video.

    `reason` says why, which decides whether retrying later is worth it:
      "disabled"          - the creator turned captions off; never retry
      "not_found"         - no transcript in an acceptable language; rarely retry
      "video_unavailable" - private, deleted or age restricted; never retry
      "blocked"           - YouTube is blocking this IP; retry later
      "failed"            - anything else; retry later
    """

    def __init__(self, video_id: str, reason: str, message: str):
        super().__init__(message)
        self.video_id = video_id
        self.reason = reason

    @property
    def retryable(self) -> bool:
        return self.reason in ("blocked", "failed")


def extract_video_id(text: str) -> str:
    """Accept a bare ID, watch?v=..., youtu.be/... or /shorts/... and return the 11-character ID."""
    text = text.strip()
    if VIDEO_ID_RE.match(text):
        return text

    url = text if "://" in text else "https://" + text
    parsed = urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]

    if parsed.hostname in ("youtu.be", "www.youtu.be") and parts:
        candidate = parts[0]
    elif parts and parts[0] in ("shorts", "live", "embed") and len(parts) >= 2:
        candidate = parts[1]
    else:
        candidate = parse_qs(parsed.query).get("v", [""])[0]

    if not VIDEO_ID_RE.match(candidate):
        raise ValueError(f"Could not find a video ID in: {text}")
    return candidate


class TranscriptScraper:
    """Picks the best available transcript for a video and returns it as a VideoTranscript."""

    def __init__(self, api: YouTubeTranscriptApi | None = None, languages: tuple[str, ...] = DEFAULT_LANGUAGES):
        self._api = api or YouTubeTranscriptApi()
        self._languages = languages

    def fetch(self, video: str | Video) -> VideoTranscript:
        """Fetch the transcript for a video ID, link, or Video object.

        Raises TranscriptUnavailable when the video has no usable transcript.
        """
        video_id = video.video_id if isinstance(video, Video) else extract_video_id(video)
        try:
            available = self._api.list(video_id)
            transcript = self._choose(available)
            fetched = transcript.fetch()
        except CouldNotRetrieveTranscript as exc:
            raise self._as_unavailable(video_id, exc) from exc

        return self._to_model(video_id, transcript, fetched)

    def _choose(self, available: TranscriptList) -> Transcript:
        """Prefer a human-written transcript, then an automatic one, then a translation."""
        try:
            return available.find_manually_created_transcript(self._languages)
        except NoTranscriptFound:
            pass
        try:
            return available.find_generated_transcript(self._languages)
        except NoTranscriptFound:
            pass

        # Nothing in the languages we asked for: translate another one if we can.
        others = list(available)
        if not others:
            raise NoTranscriptFound(available.video_id, list(self._languages), available)
        translatable = next((item for item in others if item.is_translatable), None)
        return translatable.translate(self._languages[0]) if translatable else others[0]

    def _to_model(self, video_id: str, transcript: Transcript, fetched: FetchedTranscript) -> VideoTranscript:
        return VideoTranscript(
            video_id=video_id,
            language=transcript.language,
            language_code=transcript.language_code,
            is_generated=transcript.is_generated,
            is_translated=transcript.language_code not in self._languages,
            segments=[
                TranscriptSegment(text=snippet.text, start=snippet.start, duration=snippet.duration)
                for snippet in fetched
            ],
        )

    @staticmethod
    def _as_unavailable(video_id: str, exc: CouldNotRetrieveTranscript) -> TranscriptUnavailable:
        reasons: list[tuple[type[Exception] | tuple[type[Exception], ...], str, str]] = [
            (TranscriptsDisabled, "disabled", "Captions are turned off for this video"),
            (NoTranscriptFound, "not_found", "No transcript in an acceptable language"),
            (AgeRestricted, "video_unavailable", "This video is age restricted"),
            ((RequestBlocked, IpBlocked), "blocked", "YouTube is blocking transcript requests from this IP"),
            (VideoUnavailable, "video_unavailable", "This video is unavailable"),
        ]
        for types, reason, message in reasons:
            if isinstance(exc, types):
                return TranscriptUnavailable(video_id, reason, f"{message} ({video_id})")
        return TranscriptUnavailable(video_id, "failed", f"Could not fetch the transcript for {video_id}: {exc}")


def fetch_latest_video_transcript(
    channel: str,
    languages: tuple[str, ...] = DEFAULT_LANGUAGES,
) -> tuple[Video, VideoTranscript]:
    """Find a channel's newest video and fetch its transcript.

    `channel` is any channel link or handle, as in channels.toml.
    """
    with build_client() as client:
        resolver = ChannelResolver(client)
        scraper = YouTubeScraper(client, resolver)
        channel_id = resolver.resolve(channel)
        _, videos = scraper.fetch_channel_videos(channel_id)

    if not videos:
        raise LookupError(f"No videos found for channel: {channel}")

    latest = videos[0]  # the feed is parsed newest first
    return latest, TranscriptScraper(languages=languages).fetch(latest.video_id)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print the transcript of a YouTube video")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--video", help="video ID or link")
    source.add_argument("--channel", help="channel link or handle; uses that channel's newest video")
    parser.add_argument("--languages", default="en", help="preferred languages, comma separated (default: en)")
    parser.add_argument("--timestamps", action="store_true", help="show a [mm:ss] marker on every line")
    parser.add_argument("--chars", type=int, default=2000, help="how much text to print (0 for all)")
    args = parser.parse_args(argv)

    sys.stdout.reconfigure(errors="replace")
    languages = tuple(code.strip() for code in args.languages.split(",") if code.strip())

    try:
        if args.channel:
            video, transcript = fetch_latest_video_transcript(args.channel, languages)
            print(f"{video.title}\n{video.url}\n")
        else:
            transcript = TranscriptScraper(languages=languages).fetch(args.video)
    except (TranscriptUnavailable, LookupError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    # YouTube's language name sometimes already says "(auto-generated)"; don't repeat it.
    labels = [] if "auto-generated" in transcript.language.lower() else ["auto-generated" if transcript.is_generated else "human-written"]
    if transcript.is_translated:
        labels.append("translated")
    suffix = f" ({', '.join(labels)})" if labels else ""
    minutes = transcript.duration / 60
    print(f"Transcript: {transcript.language}{suffix} - {len(transcript.segments)} lines, ~{minutes:.0f} min\n")

    text = transcript.to_text(timestamps=args.timestamps)
    print(text if args.chars <= 0 else text[: args.chars])
    if 0 < args.chars < len(text):
        print(f"\n... ({len(text) - args.chars:,} more characters)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
