"""Data shapes shared by the scrapers."""

from datetime import datetime

from pydantic import BaseModel, Field


class ChannelConfig(BaseModel):
    """One entry from channels.toml, exactly as the user wrote it."""

    url: str
    tags: list[str] = Field(default_factory=list)


class Channel(BaseModel):
    """A channel after its link has been resolved to a real channel ID."""

    channel_id: str
    title: str
    url: str
    tags: list[str] = Field(default_factory=list)


class Video(BaseModel):
    video_id: str
    channel_id: str
    channel_title: str
    title: str
    url: str
    published_at: datetime  # timezone-aware, UTC
    updated_at: datetime | None = None
    description: str = ""
    thumbnail_url: str | None = None
    views: int | None = None


class ChannelResult(BaseModel):
    """What scraping one configured channel produced: videos or an error, never both."""

    config: ChannelConfig
    channel: Channel | None = None
    videos: list[Video] = Field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


class TranscriptSegment(BaseModel):
    """One spoken line of a transcript."""

    text: str
    start: float  # seconds from the beginning of the video
    duration: float


class VideoTranscript(BaseModel):
    video_id: str
    language: str  # human readable, e.g. "English (auto-generated)"
    language_code: str  # e.g. "en"
    is_generated: bool  # True when YouTube produced it automatically
    is_translated: bool = False
    segments: list[TranscriptSegment] = Field(default_factory=list)

    @property
    def text(self) -> str:
        """The whole transcript as one block of plain text."""
        return " ".join(segment.text.strip() for segment in self.segments if segment.text.strip())

    @property
    def duration(self) -> float:
        """Seconds covered by the transcript, which is roughly the video length."""
        if not self.segments:
            return 0.0
        last = self.segments[-1]
        return last.start + last.duration

    def to_text(self, timestamps: bool = False) -> str:
        """Plain text, optionally with a [mm:ss] marker in front of every line."""
        if not timestamps:
            return self.text
        return "\n".join(f"[{format_timestamp(segment.start)}] {segment.text}" for segment in self.segments)


def format_timestamp(seconds: float) -> str:
    """Seconds as mm:ss, or h:mm:ss once the video passes an hour."""
    total = int(seconds)
    hours, minutes, secs = total // 3600, (total % 3600) // 60, total % 60
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"
