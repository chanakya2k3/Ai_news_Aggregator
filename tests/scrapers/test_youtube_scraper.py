from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

from app.scrapers.models import ChannelConfig
from app.scrapers.youtube_resolver import ChannelResolver
from app.scrapers.youtube_scraper import FEED_URL, YouTubeScraper, load_channel_configs, parse_feed

FIXTURES = Path(__file__).parent / "fixtures"
FEED_XML = (FIXTURES / "feed.xml").read_text(encoding="utf-8")
CHANNEL_ID = "UCaaaaaaaaaaaaaaaaaaaaaa"
BROKEN_ID = "UCzzzzzzzzzzzzzzzzzzzzzz"


def test_parse_feed():
    title, videos = parse_feed(FEED_XML, CHANNEL_ID)

    assert title == "Test Channel"
    assert [video.video_id for video in videos] == ["newer000002", "older000001"]  # newest first

    newest = videos[0]
    assert newest.title == "Newer video 🚀"
    assert newest.url == "https://www.youtube.com/watch?v=newer000002"
    assert newest.channel_id == CHANNEL_ID
    assert newest.channel_title == "Test Channel"
    assert newest.published_at == datetime(2026, 9, 13, 18, 30, tzinfo=UTC)
    assert newest.description == "A brand new upload."
    assert newest.thumbnail_url == "https://i2.ytimg.com/vi/newer000002/hqdefault.jpg"
    assert newest.views == 0
    assert videos[1].views == 1234


def test_load_channel_configs(tmp_path):
    path = tmp_path / "channels.toml"
    path.write_text(
        '[[channels]]\nurl = "@One"\ntags = ["llm"]\n\n[[channels]]\nurl = "@Two"\n',
        encoding="utf-8",
    )
    configs = load_channel_configs(path)
    assert configs == [ChannelConfig(url="@One", tags=["llm"]), ChannelConfig(url="@Two")]


@respx.mock
def test_scrape_filters_by_date_and_isolates_failures():
    respx.get(FEED_URL, params={"channel_id": CHANNEL_ID}).mock(return_value=httpx.Response(200, text=FEED_XML))
    respx.get(FEED_URL, params={"channel_id": BROKEN_ID}).mock(return_value=httpx.Response(404))
    configs = [
        ChannelConfig(url=BROKEN_ID),
        ChannelConfig(url="https://youtu.be/abc123"),  # a video link, not a channel
        ChannelConfig(url=f"https://www.youtube.com/channel/{CHANNEL_ID}", tags=["llm"]),
    ]

    with httpx.Client() as client:
        scraper = YouTubeScraper(client, ChannelResolver(client))
        results = scraper.scrape(configs, since=datetime(2026, 9, 10, tzinfo=UTC))

    broken, video_link, good = results
    assert not broken.ok and "No feed" in broken.error
    assert not video_link.ok and "video link" in video_link.error
    assert good.ok
    assert good.channel.title == "Test Channel"
    assert good.channel.tags == ["llm"]
    assert [video.video_id for video in good.videos] == ["newer000002"]


@respx.mock
def test_scrape_reports_http_errors():
    respx.get(FEED_URL).mock(return_value=httpx.Response(500))
    with httpx.Client() as client:
        result = YouTubeScraper(client, ChannelResolver(client)).scrape_channel(ChannelConfig(url=CHANNEL_ID))
    assert result.error == f"YouTube returned HTTP 500 for the feed of {CHANNEL_ID}"


def test_missing_channels_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_channel_configs(tmp_path / "nope.toml")
