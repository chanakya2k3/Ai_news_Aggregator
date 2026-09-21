"""Build the digest email from what's in the database and send (or preview) it.

    uv run python -m app.services.digest --dry-run        write out/digest_*.html
    uv run python -m app.services.digest --dry-run --open  ... and open it in the browser
    uv run python -m app.services.digest                   send it by email

Videos are picked by when they were discovered, so a digest covers everything
found since the last one (default: the last 24 hours).
"""

import argparse
import sys
from datetime import UTC, datetime, timedelta

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.config import settings
from app.database.models import Video
from app.database.session import get_session
from app.notify.email_sender import EmailSender, FileSender, SmtpSender

TEMPLATE_DIR = __import__("pathlib").Path(__file__).resolve().parent.parent / "notify" / "templates"

jinja = Environment(
    loader=FileSystemLoader(TEMPLATE_DIR),
    autoescape=select_autoescape(["html", "j2"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


def recent_videos(session: Session, hours: float) -> list[Video]:
    """Videos discovered in the last N hours, newest first."""
    since = datetime.now(UTC) - timedelta(hours=hours)
    statement = (
        select(Video)
        .options(joinedload(Video.channel))
        .where(Video.discovered_at >= since)
        .order_by(Video.published_at.desc())
    )
    return list(session.execute(statement).unique().scalars())


def build_context(videos: list[Video]) -> dict:
    """Group videos by channel and shape them for the templates."""
    groups: dict[int, dict] = {}
    for video in videos:
        group = groups.setdefault(
            video.channel_id,
            {
                "channel": {
                    "title": video.channel.title,
                    "url": video.channel.url,
                    "tags": list(video.channel.tags or []),
                },
                "videos": [],
            },
        )
        group["videos"].append(
            {
                "title": video.title,
                "url": video.url,
                "views": video.views,
                "thumbnail_url": video.thumbnail_url,
                "published_local": video.published_at.astimezone().strftime("%a %d %b, %H:%M"),
                "summary": None,  # filled in once the Ollama summarizer exists
            }
        )

    return {
        "title": "Your AI news digest",
        "groups": list(groups.values()),
        "video_count": len(videos),
        "channel_count": len(groups),
        "generated_at": datetime.now().strftime("%d %b %Y, %H:%M"),
    }


def render(context: dict) -> tuple[str, str, str]:
    """Return (subject, html, text)."""
    subject = f"AI news digest - {context['video_count']} new video(s)"
    html = jinja.get_template("digest.html.j2").render(**context)
    text = jinja.get_template("digest.txt.j2").render(**context)
    return subject, html, text


def run(hours: float, sender: EmailSender, send_when_empty: bool = False) -> int:
    with get_session() as session:
        videos = recent_videos(session, hours)
        context = build_context(videos)

    if not videos and not send_when_empty:
        print(f"No new videos in the last {hours:g} hours. Nothing sent.")
        return 0

    subject, html, text = render(context)
    result = sender.send(subject, html, text)
    print(f"{context['video_count']} video(s) from {context['channel_count']} channel(s): {result}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Send (or preview) the digest email")
    parser.add_argument("--hours", type=float, default=24, help="how far back to look (default: 24)")
    parser.add_argument("--dry-run", action="store_true", help="write the email to out/ instead of sending")
    parser.add_argument("--open", action="store_true", help="with --dry-run, open the preview in your browser")
    parser.add_argument("--send-when-empty", action="store_true", help="send even when there are no new videos")
    args = parser.parse_args(argv)

    sys.stdout.reconfigure(errors="replace")

    if args.dry_run:
        sender: EmailSender = FileSender(open_in_browser=args.open)
    elif not settings.email_configured:
        print(
            "Email is not set up yet. Add SMTP_HOST, SMTP_USER, SMTP_PASSWORD and EMAIL_TO to .env,\n"
            "or run with --dry-run to preview the email instead.",
            file=sys.stderr,
        )
        return 2
    else:
        sender = SmtpSender()

    try:
        return run(args.hours, sender, args.send_when_empty)
    except Exception as exc:  # smtplib and template errors both land here
        print(f"[FAILED] {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
