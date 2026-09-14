"""Turn whatever channel link the user pasted into a YouTube channel ID (UC...)."""

import json
import re
from pathlib import Path
from urllib.parse import urlparse

import httpx

YOUTUBE_BASE = "https://www.youtube.com"
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com"}

CHANNEL_ID_RE = re.compile(r"^UC[\w-]{22}$")

# Patterns that identify the page's *own* channel. Order matters: the page also
# contains "channelId" values for other channels, so never use a loose match.
PAGE_CHANNEL_ID_PATTERNS = [
    re.compile(r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[\w-]{22})"'),
    re.compile(r'<meta itemprop="identifier" content="(UC[\w-]{22})"'),
    re.compile(r'"externalId":"(UC[\w-]{22})"'),
]

# Browser-like headers; the SOCS cookie skips the EU cookie-consent interstitial.
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/140.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
DEFAULT_COOKIES = {"SOCS": "CAI"}


class ChannelResolveError(Exception):
    """The link could not be turned into a channel ID."""


def extract_channel_id(text: str) -> str | None:
    """Return the channel ID if it is written directly in the input, without any network call."""
    text = text.strip()
    if CHANNEL_ID_RE.match(text):
        return text
    parts = _path_parts(text)
    if len(parts) >= 2 and parts[0] == "channel" and CHANNEL_ID_RE.match(parts[1]):
        return parts[1]
    return None


def channel_page_url(text: str) -> str:
    """Normalise a handle / custom / legacy link to the channel page we need to download.

    Accepts: @handle, handle, youtube.com/@handle(/videos...), youtube.com/c/Name, youtube.com/user/Name
    """
    text = text.strip()
    if not text:
        raise ChannelResolveError("Empty channel link")

    if "/" not in text and "." not in text:
        handle = text.removeprefix("@")
        return f"{YOUTUBE_BASE}/@{handle}"

    parts = _path_parts(text)
    if parts and parts[0].startswith("@"):
        return f"{YOUTUBE_BASE}/{parts[0]}"
    if len(parts) >= 2 and parts[0] in ("c", "user"):
        return f"{YOUTUBE_BASE}/{parts[0]}/{parts[1]}"
    if parts and parts[0] in ("watch", "shorts", "live") or "youtu.be" in text:
        raise ChannelResolveError(f"This is a video link, not a channel link: {text}")
    raise ChannelResolveError(f"Not a recognised YouTube channel link: {text}")


def parse_channel_id_from_page(html: str) -> str | None:
    for pattern in PAGE_CHANNEL_ID_PATTERNS:
        match = pattern.search(html)
        if match:
            return match.group(1)
    return None


def _path_parts(text: str) -> list[str]:
    if "://" not in text:
        text = "https://" + text
    parsed = urlparse(text)
    if parsed.hostname not in YOUTUBE_HOSTS:
        return []
    return [part for part in parsed.path.split("/") if part]


class ChannelResolver:
    """Resolves links to channel IDs, caching page lookups in a JSON file."""

    def __init__(self, client: httpx.Client, cache_path: Path | None = None):
        self._client = client
        self._cache_path = cache_path
        self._cache: dict[str, str] = self._load_cache()

    def resolve(self, text: str) -> str:
        channel_id = extract_channel_id(text)
        if channel_id:
            return channel_id

        page_url = channel_page_url(text)
        cache_key = page_url.lower()
        if cache_key in self._cache:
            return self._cache[cache_key]

        channel_id = self._lookup_page(page_url)
        self._cache[cache_key] = channel_id
        self._save_cache()
        return channel_id

    def _lookup_page(self, page_url: str) -> str:
        try:
            response = self._client.get(page_url)
        except httpx.HTTPError as exc:
            raise ChannelResolveError(f"Could not reach {page_url}: {exc}") from exc

        if response.status_code == 404:
            raise ChannelResolveError(f"Channel not found: {page_url}")
        if response.status_code != 200:
            raise ChannelResolveError(f"YouTube returned HTTP {response.status_code} for {page_url}")
        if response.url.host == "consent.youtube.com":
            raise ChannelResolveError(f"YouTube showed a cookie-consent page for {page_url}")

        channel_id = parse_channel_id_from_page(response.text)
        if not channel_id:
            raise ChannelResolveError(f"Could not find the channel ID on {page_url}")
        return channel_id

    def _load_cache(self) -> dict[str, str]:
        if not self._cache_path or not self._cache_path.exists():
            return {}
        try:
            return json.loads(self._cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}  # a broken cache only costs a re-lookup

    def _save_cache(self) -> None:
        if not self._cache_path:
            return
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._cache_path.write_text(json.dumps(self._cache, indent=2, sort_keys=True), encoding="utf-8")
