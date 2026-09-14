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
