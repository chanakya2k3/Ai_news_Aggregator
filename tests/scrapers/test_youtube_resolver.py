import httpx
import pytest
import respx

from app.scrapers.youtube_resolver import (
    ChannelResolveError,
    ChannelResolver,
    channel_page_url,
    extract_channel_id,
    parse_channel_id_from_page,
)

CHANNEL_ID = "UCaaaaaaaaaaaaaaaaaaaaaa"
OTHER_ID = "UCbbbbbbbbbbbbbbbbbbbbbb"

CHANNEL_PAGE = f"""
<html><head>
<script>var ytInitialData = {{"channelId":"{OTHER_ID}"}};</script>
<link rel="canonical" href="https://www.youtube.com/channel/{CHANNEL_ID}">
<meta itemprop="identifier" content="{CHANNEL_ID}">
</head></html>
"""


@pytest.mark.parametrize(
    "text",
    [
        CHANNEL_ID,
        f"https://www.youtube.com/channel/{CHANNEL_ID}",
        f"youtube.com/channel/{CHANNEL_ID}/videos",
        f"https://m.youtube.com/channel/{CHANNEL_ID}?si=abc",
    ],
)
def test_extract_channel_id_from_direct_links(text):
    assert extract_channel_id(text) == CHANNEL_ID


@pytest.mark.parametrize("text", ["@SomeHandle", "https://www.youtube.com/@SomeHandle", "https://example.com/channel/" + CHANNEL_ID])
def test_extract_channel_id_returns_none_when_lookup_needed(text):
    assert extract_channel_id(text) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("@SomeHandle", "https://www.youtube.com/@SomeHandle"),
        ("SomeHandle", "https://www.youtube.com/@SomeHandle"),
        ("https://www.youtube.com/@SomeHandle/videos", "https://www.youtube.com/@SomeHandle"),
        ("youtube.com/@SomeHandle", "https://www.youtube.com/@SomeHandle"),
        ("https://www.youtube.com/c/SomeName", "https://www.youtube.com/c/SomeName"),
        ("https://www.youtube.com/user/SomeName/featured", "https://www.youtube.com/user/SomeName"),
    ],
)
def test_channel_page_url(text, expected):
    assert channel_page_url(text) == expected


@pytest.mark.parametrize(
    "text",
    ["https://www.youtube.com/watch?v=abc", "https://youtu.be/abc", "https://example.com/@x", "", "https://www.youtube.com/"],
)
def test_channel_page_url_rejects_non_channel_links(text):
    with pytest.raises(ChannelResolveError):
        channel_page_url(text)


def test_page_parsing_ignores_other_channel_ids():
    assert parse_channel_id_from_page(CHANNEL_PAGE) == CHANNEL_ID


@respx.mock
def test_resolve_handle_uses_page_and_caches(tmp_path):
    route = respx.get("https://www.youtube.com/@SomeHandle").mock(return_value=httpx.Response(200, text=CHANNEL_PAGE))
    cache = tmp_path / "ids.json"

    with httpx.Client() as client:
        assert ChannelResolver(client, cache).resolve("@SomeHandle") == CHANNEL_ID
        # A new resolver reads the cache file instead of downloading the page again.
        assert ChannelResolver(client, cache).resolve("https://www.youtube.com/@somehandle") == CHANNEL_ID

    assert route.call_count == 1


@respx.mock
def test_resolve_direct_id_makes_no_request():
    with httpx.Client() as client:
        assert ChannelResolver(client).resolve(f"https://www.youtube.com/channel/{CHANNEL_ID}") == CHANNEL_ID
    assert not respx.calls


@respx.mock
def test_resolve_missing_channel():
    respx.get("https://www.youtube.com/@Nope").mock(return_value=httpx.Response(404))
    with httpx.Client() as client, pytest.raises(ChannelResolveError, match="not found"):
        ChannelResolver(client).resolve("@Nope")


@respx.mock
def test_resolve_page_without_id():
    respx.get("https://www.youtube.com/@Weird").mock(return_value=httpx.Response(200, text="<html></html>"))
    with httpx.Client() as client, pytest.raises(ChannelResolveError, match="Could not find"):
        ChannelResolver(client).resolve("@Weird")
