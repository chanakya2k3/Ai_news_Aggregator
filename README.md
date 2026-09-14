# Ai_news_Aggregator

A local-first Python app that watches the YouTube channels you follow, finds new videos, and (soon) emails you digests on your own schedule.

Everything runs on your machine: Python backend, PostgreSQL, Docker. No hosted databases.

> **Status:** early development. The YouTube scraper works today. Database, email digests, scheduling and AI summaries are planned. See [docs/PLAN.md](docs/PLAN.md).

## Features

**Working now**
- Paste any channel link: `@handle`, `/channel/UC…`, `/c/Name`, `/user/Name`
- Resolves links to channel IDs and caches them locally
- Fetches each channel's latest videos (title, link, publish time, description, thumbnail, views) from YouTube's RSS feed, with no API key
- Tags per channel
- One broken channel never stops the others

**Planned**
- PostgreSQL storage with no duplicate notifications
- Email digests: instant, hourly, daily or weekly, in your timezone
- Background worker in Docker Compose
- AI video summaries from transcripts, using a local LLM

## Requirements

- Python 3.14
- [uv](https://docs.astral.sh/uv/)

## Setup

```bash
git clone https://github.com/chanakya2k3/Ai_news_Aggregator.git
cd Ai_news_Aggregator
uv sync
```

`uv sync` creates `.venv` and installs all dependencies.

## Usage

### 1. Add your channels

Edit [`channels.toml`](channels.toml):

```toml
[[channels]]
url = "https://www.youtube.com/@AndrejKarpathy"
tags = ["llm"]

[[channels]]
url = "https://www.youtube.com/channel/UCXUPKJO5MZQN11PqgIvyuvQ"
```

- `url` accepts any channel link. Video links (`watch?v=`, `youtu.be/`) are rejected.
- `tags` is optional.
- Comment out an entry with `#` to pause it.

### 2. Run the scraper

```bash
# latest videos from every channel
uv run python -m app.scrapers.youtube_scraper

# only videos from the last 24 hours
uv run python -m app.scrapers.youtube_scraper --hours 24

# use a different channels file
uv run python -m app.scrapers.youtube_scraper --channels path/to/channels.toml
```

Example output:

```
== Andrej Karpathy (UCXUPKJO5MZQN11PqgIvyuvQ)  [llm] - 15 video(s)
  2025-02-28 03:59  How I use LLMs
                    https://www.youtube.com/watch?v=EWvNQjAaOHw  (2,716,479 views)
...
Done: 1 channel(s) OK, 0 failed.
```

The exit code is `0` when every channel succeeded and `1` when any failed.

## Running tests

```bash
uv run pytest
```

Tests use mocked HTTP and never call YouTube.

## Project structure

```
├── app/
│   ├── agent/                 # AI summaries (planned)
│   ├── scrapers/
│   │   ├── models.py          # Channel, Video, ChannelResult
│   │   ├── youtube_resolver.py
│   │   └── youtube_scraper.py
│   └── services/              # DB, email, scheduling (planned)
├── tests/scrapers/
├── docs/PLAN.md               # architecture and roadmap
├── channels.toml              # your channel list
└── pyproject.toml
```

## How it works

1. **Resolve.** A `/channel/UC…` link already contains the channel ID. Other link types download the channel page once to read the ID, and the result is saved in `.cache/channel_ids.json`.
2. **Fetch.** `https://www.youtube.com/feeds/videos.xml?channel_id=UC…` returns the channel's ~15 most recent uploads.
3. **Parse.** Entries become typed `Video` objects, with times in UTC.

## Limitations

- The RSS feed only lists each channel's ~15 newest uploads.
- Shorts and live streams aren't marked separately.
- Results are printed only. Storage and email are coming.

## Roadmap

See [docs/PLAN.md](docs/PLAN.md) for the full architecture. In order:

1. Docker Compose + PostgreSQL
2. Store channels and videos
3. Email digests
4. Scheduling worker
5. Hardening: logs, alerts, backups
6. AI summaries
