"""Tests for the transcript scraper, using a fake API so nothing touches the network."""

from dataclasses import dataclass

import pytest
from youtube_transcript_api import NoTranscriptFound, TranscriptsDisabled, VideoUnavailable

from app.scrapers.models import VideoTranscript
from app.scrapers.transcript_scraper import (
    TranscriptScraper,
    TranscriptUnavailable,
    extract_video_id,
)

VIDEO_ID = "EWvNQjAaOHw"


@dataclass
class FakeSnippet:
    text: str
    start: float
    duration: float


@dataclass
class FakeTranscript:
    """Stands in for the library's Transcript object."""

    language: str = "English"
    language_code: str = "en"
    is_generated: bool = False
    is_translatable: bool = False
    snippets: tuple[FakeSnippet, ...] = (
        FakeSnippet("hello there", 0.0, 2.5),
        FakeSnippet("second line", 2.5, 3.0),
    )

    def fetch(self):
        return list(self.snippets)

    def translate(self, language_code: str) -> "FakeTranscript":
        return FakeTranscript(language="English (translated)", language_code=language_code, is_generated=True)


class FakeTranscriptList:
    """Stands in for the library's TranscriptList object."""

    def __init__(self, manual=None, generated=None, others=()):
        self.video_id = VIDEO_ID
        self._manual = manual
        self._generated = generated
        self._others = list(others)

    def __iter__(self):
        return iter(self._others)

    def find_manually_created_transcript(self, languages):
        if self._manual is None:
            raise NoTranscriptFound(self.video_id, list(languages), self)
        return self._manual

    def find_generated_transcript(self, languages):
        if self._generated is None:
            raise NoTranscriptFound(self.video_id, list(languages), self)
        return self._generated


class FakeApi:
    def __init__(self, result):
        self._result = result
        self.calls: list[str] = []

    def list(self, video_id: str):
        self.calls.append(video_id)
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


@pytest.mark.parametrize(
    "text",
    [
        VIDEO_ID,
        f"https://www.youtube.com/watch?v={VIDEO_ID}",
        f"https://www.youtube.com/watch?v={VIDEO_ID}&t=42s",
        f"https://youtu.be/{VIDEO_ID}",
        f"youtube.com/shorts/{VIDEO_ID}",
        f"  https://m.youtube.com/watch?v={VIDEO_ID}  ",
    ],
)
def test_extract_video_id(text):
    assert extract_video_id(text) == VIDEO_ID


@pytest.mark.parametrize("text", ["", "not a link", "https://www.youtube.com/@Handle", "https://youtu.be/tooshort"])
def test_extract_video_id_rejects_bad_input(text):
    with pytest.raises(ValueError):
        extract_video_id(text)


def test_prefers_human_written_transcript():
    manual = FakeTranscript(language="English", is_generated=False)
    generated = FakeTranscript(language="English (auto-generated)", is_generated=True)
    scraper = TranscriptScraper(api=FakeApi(FakeTranscriptList(manual=manual, generated=generated)))

    transcript = scraper.fetch(VIDEO_ID)

    assert isinstance(transcript, VideoTranscript)
    assert transcript.is_generated is False
    assert transcript.video_id == VIDEO_ID
    assert transcript.text == "hello there second line"
    assert transcript.duration == pytest.approx(5.5)


def test_falls_back_to_generated_transcript():
    generated = FakeTranscript(language="English (auto-generated)", is_generated=True)
    scraper = TranscriptScraper(api=FakeApi(FakeTranscriptList(generated=generated)))

    assert scraper.fetch(VIDEO_ID).is_generated is True


def test_translates_when_no_transcript_in_wanted_language():
    spanish = FakeTranscript(language="Spanish", language_code="es", is_generated=True, is_translatable=True)
    scraper = TranscriptScraper(api=FakeApi(FakeTranscriptList(others=[spanish])))

    transcript = scraper.fetch(VIDEO_ID)

    assert transcript.language_code == "en"
    assert transcript.is_translated is False  # translated into the language we asked for


def test_uses_untranslatable_transcript_as_is():
    german = FakeTranscript(language="German", language_code="de", is_generated=True, is_translatable=False)
    scraper = TranscriptScraper(api=FakeApi(FakeTranscriptList(others=[german])))

    transcript = scraper.fetch(VIDEO_ID)

    assert transcript.language_code == "de"
    assert transcript.is_translated is True


def test_accepts_a_link_instead_of_an_id():
    api = FakeApi(FakeTranscriptList(manual=FakeTranscript()))
    TranscriptScraper(api=api).fetch(f"https://youtu.be/{VIDEO_ID}")
    assert api.calls == [VIDEO_ID]


@pytest.mark.parametrize(
    ("error", "reason", "retryable"),
    [
        (TranscriptsDisabled(VIDEO_ID), "disabled", False),
        (VideoUnavailable(VIDEO_ID), "video_unavailable", False),
    ],
)
def test_errors_are_reported_with_a_reason(error, reason, retryable):
    scraper = TranscriptScraper(api=FakeApi(error))

    with pytest.raises(TranscriptUnavailable) as caught:
        scraper.fetch(VIDEO_ID)

    assert caught.value.reason == reason
    assert caught.value.retryable is retryable
    assert caught.value.video_id == VIDEO_ID


def test_no_transcript_at_all():
    scraper = TranscriptScraper(api=FakeApi(FakeTranscriptList()))

    with pytest.raises(TranscriptUnavailable) as caught:
        scraper.fetch(VIDEO_ID)

    assert caught.value.reason == "not_found"


def test_to_text_with_timestamps():
    scraper = TranscriptScraper(api=FakeApi(FakeTranscriptList(manual=FakeTranscript())))

    lines = scraper.fetch(VIDEO_ID).to_text(timestamps=True).splitlines()

    assert lines == ["[0:00] hello there", "[0:02] second line"]
