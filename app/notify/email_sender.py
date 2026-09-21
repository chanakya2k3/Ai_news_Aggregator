"""Sending the digest email.

Two senders share one interface:
  SmtpSender - the real thing, over SMTP with STARTTLS
  FileSender - writes the email to a file instead, for previewing without credentials
"""

import smtplib
import webbrowser
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Protocol

from app.config import settings

PREVIEW_DIR = Path("out")


class EmailSender(Protocol):
    def send(self, subject: str, html: str, text: str) -> str:
        """Send the message and return a short description of what happened."""
        ...


class SmtpSender:
    """Sends through an SMTP server, e.g. Gmail with an App Password."""

    def __init__(self, host: str = "", port: int = 0, user: str = "", password: str = "", sender: str = "", recipient: str = ""):
        self.host = host or settings.smtp_host
        self.port = port or settings.smtp_port
        self.user = user or settings.smtp_user
        self.password = password or settings.smtp_password
        self.sender = sender or settings.email_from or self.user
        self.recipient = recipient or settings.email_to

    def send(self, subject: str, html: str, text: str) -> str:
        if not (self.host and self.user and self.password and self.recipient):
            raise RuntimeError(
                "Email is not configured. Fill in SMTP_HOST, SMTP_USER, SMTP_PASSWORD and EMAIL_TO in .env"
            )

        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self.sender
        message["To"] = self.recipient
        message.set_content(text)
        message.add_alternative(html, subtype="html")

        with smtplib.SMTP(self.host, self.port, timeout=30) as server:
            server.starttls()
            server.login(self.user, self.password)
            server.send_message(message)

        return f"sent to {self.recipient}"


class FileSender:
    """Writes the email to out/ instead of sending it, so you can look at it first."""

    def __init__(self, directory: Path = PREVIEW_DIR, open_in_browser: bool = False):
        self.directory = directory
        self.open_in_browser = open_in_browser

    def send(self, subject: str, html: str, text: str) -> str:
        self.directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = self.directory / f"digest_{stamp}.html"
        path.write_text(html, encoding="utf-8")
        (self.directory / f"digest_{stamp}.txt").write_text(f"Subject: {subject}\n\n{text}", encoding="utf-8")

        if self.open_in_browser:
            webbrowser.open(path.resolve().as_uri())
        return f"written to {path}"
