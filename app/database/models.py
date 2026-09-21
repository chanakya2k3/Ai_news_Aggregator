"""SQLAlchemy models: the tables that hold channels, videos and transcripts."""

from datetime import UTC, datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Channel(Base):
    """A YouTube channel we follow."""

    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(primary_key=True)
    youtube_channel_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(255))
    url: Mapped[str] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    videos: Mapped[list["Video"]] = relationship(back_populates="channel", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Channel {self.title!r} ({self.youtube_channel_id})>"


class Video(Base):
    """One video found in a channel's feed.

    `youtube_video_id` is unique, so the database itself prevents the same video
    from being stored (and emailed) twice.
    """

    __tablename__ = "videos"

    id: Mapped[int] = mapped_column(primary_key=True)
    youtube_video_id: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), index=True)

    title: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, default="")
    thumbnail_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    views: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    channel: Mapped[Channel] = relationship(back_populates="videos")
    transcript: Mapped["Transcript | None"] = relationship(
        back_populates="video", cascade="all, delete-orphan", uselist=False
    )

    def __repr__(self) -> str:
        return f"<Video {self.youtube_video_id} {self.title[:40]!r}>"


class TranscriptStatus:
    """Values for Transcript.status (plain strings, easy to read in the database)."""

    OK = "ok"
    PENDING = "pending"  # captions not ready yet; try again later
    UNAVAILABLE = "unavailable"  # disabled or missing for good; never retry
    FAILED = "failed"  # something went wrong; retry later


class Transcript(Base):
    """The captions of a video, plus why fetching failed when it did."""

    __tablename__ = "transcripts"

    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"), unique=True, index=True)

    status: Mapped[str] = mapped_column(String(16), default=TranscriptStatus.PENDING, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    language: Mapped[str | None] = mapped_column(String(64), nullable=True)
    language_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    is_generated: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    is_translated: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Kept as JSON so the [mm:ss] markers survive for summaries and email links.
    segments: Mapped[list[dict] | None] = mapped_column(JSONB, nullable=True)

    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    video: Mapped[Video] = relationship(back_populates="transcript")

    def __repr__(self) -> str:
        return f"<Transcript video_id={self.video_id} status={self.status}>"
