"""The handful of database operations the app needs.

Keeping the queries here means the rest of the app never writes SQL, and there is
one place to change when the schema moves.
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.database.models import Channel, Transcript, TranscriptStatus, Video
from app.scrapers import models as scraped


def upsert_channel(session: Session, channel: scraped.Channel) -> Channel:
    """Insert the channel, or update its title and tags if we already have it."""
    statement = (
        insert(Channel)
        .values(
            youtube_channel_id=channel.channel_id,
            title=channel.title,
            url=channel.url,
            tags=channel.tags,
        )
        .on_conflict_do_update(
            index_elements=[Channel.youtube_channel_id],
            set_={"title": channel.title, "tags": channel.tags},
        )
        .returning(Channel)
    )
    return session.execute(statement).scalar_one()


def add_new_videos(session: Session, channel: Channel, videos: list[scraped.Video]) -> list[Video]:
    """Store videos we haven't seen before and return only those new rows.

    Videos already in the table are skipped by the unique youtube_video_id, so
    running this repeatedly is safe.
    """
    if not videos:
        return []

    rows = [
        {
            "youtube_video_id": video.video_id,
            "channel_id": channel.id,
            "title": video.title,
            "url": video.url,
            "description": video.description,
            "thumbnail_url": video.thumbnail_url,
            "views": video.views,
            "published_at": video.published_at,
        }
        for video in videos
    ]
    statement = (
        insert(Video)
        .values(rows)
        .on_conflict_do_nothing(index_elements=[Video.youtube_video_id])
        .returning(Video)
    )
    new_videos = list(session.execute(statement).scalars())
    channel.last_checked_at = datetime.now(UTC)
    return new_videos


def videos_needing_transcript(session: Session, limit: int = 20, max_attempts: int = 3) -> list[Video]:
    """Videos with no transcript yet, or one worth retrying, newest first."""
    retryable = (TranscriptStatus.PENDING, TranscriptStatus.FAILED)
    statement = (
        select(Video)
        .outerjoin(Transcript)
        .where(
            (Transcript.id.is_(None))
            | ((Transcript.status.in_(retryable)) & (Transcript.attempts < max_attempts))
        )
        .order_by(Video.published_at.desc())
        .limit(limit)
    )
    return list(session.execute(statement).scalars())


def save_transcript(
    session: Session,
    video: Video,
    transcript: scraped.VideoTranscript | None = None,
    status: str = TranscriptStatus.OK,
    error: str | None = None,
) -> Transcript:
    """Record the outcome of a transcript attempt, whether it worked or not."""
    row = session.execute(select(Transcript).where(Transcript.video_id == video.id)).scalar_one_or_none()
    if row is None:
        row = Transcript(video_id=video.id)
        session.add(row)

    row.status = status
    row.attempts += 1
    row.error = error

    if transcript is not None:
        row.language = transcript.language
        row.language_code = transcript.language_code
        row.is_generated = transcript.is_generated
        row.is_translated = transcript.is_translated
        row.text = transcript.text
        row.segments = [segment.model_dump() for segment in transcript.segments]
        row.fetched_at = datetime.now(UTC)

    return row


def get_channel(session: Session, youtube_channel_id: str) -> Channel | None:
    return session.execute(
        select(Channel).where(Channel.youtube_channel_id == youtube_channel_id)
    ).scalar_one_or_none()


def count_rows(session: Session) -> dict[str, int]:
    """Row counts, handy for the CLI and for checking things worked."""
    from sqlalchemy import func

    return {
        "channels": session.execute(select(func.count()).select_from(Channel)).scalar_one(),
        "videos": session.execute(select(func.count()).select_from(Video)).scalar_one(),
        "transcripts": session.execute(select(func.count()).select_from(Transcript)).scalar_one(),
    }
