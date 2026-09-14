# AI News Aggregator — Architecture & Execution Plan

A local-only Python app that watches YouTube channels you've tagged, finds new videos,
and emails you updates on a schedule you choose.

**Constraints:** runs only on your machine · PostgreSQL (local) · Python backend · Docker Compose · no hosted/cloud databases.

---

## 1. Goals and non-goals

**Goals (v1)**
- Keep a list of YouTube channels, grouped by tags (e.g. `llm`, `research`, `tools`).
- Check those channels for new uploads regularly.
- Send email digests on a schedule you set: instant, hourly, daily at HH:MM, or weekly.
- Never email the same video twice for the same schedule. Don't flood you with old videos when you add a channel.
- Survive reboots, network failures and YouTube hiccups without losing or repeating anything.

**Non-goals (v1)**
- Multiple users, auth, or a hosted web UI.
- Downloading videos.
- Anything that needs a cloud database.

**Later (optional)**
- Transcript + AI summary for each video (a local LLM through Ollama keeps it fully local).
- Keyword filters, skipping Shorts, a small local web UI for settings.

---

## 2. Key design decisions

| # | Decision | Choice | Why |
|---|----------|--------|-----|
| D1 | How to find new videos | **YouTube RSS feed** (`/feeds/videos.xml?channel_id=…`) as the main source; **YouTube Data API v3** as optional extra detail | RSS is free, needs no key, and has no quota. It returns the latest ~15 uploads, which is plenty when you poll every 15–30 min. The API adds duration, Shorts/live detection and handle→ID lookup. Avoid `search.list` (100 quota units per call). `playlistItems.list` and `videos.list` cost 1 unit each. |
| D2 | Where source code plugs in | A `VideoSource` protocol with `RssSource` and `YouTubeApiSource` implementations | You can swap or combine sources without touching the ingest code. Fake sources make testing easy. |
| D3 | Scheduling | A stateless, idempotent **`tick`** command. In Docker, an **`ann worker`** loop in the `app` container calls it every 15 min. Without Docker, **Windows Task Scheduler** runs `ann tick`. | There's one unit of work (`tick`) and two ways to trigger it. `tick` keeps no state in memory, so container restarts, reboots and sleep are all safe, and a missed tick catches up on the next one. The logic that decides what's due is a pure function, so it's easy to test. |
| D11 | Containers | **Docker Compose** with `db` (Postgres 17), `mailpit` (dev email), `app` (worker), a one-shot `migrate`, and `ollama` behind a profile (phase 7) | One command (`docker compose up -d`) brings up the whole stack. No native Postgres install. Named volumes keep data safe across rebuilds, and `restart: unless-stopped` plus Docker Desktop starting at login keeps it running. Everything stays on your machine. |
| D4 | Checking vs. emailing | **Kept separate.** Checking fills `videos`. Emailing reads unsent videos for each schedule. | You can check often and email rarely, and one video can go to several schedules. |
| D5 | Database access | SQLAlchemy 2.0 (typed ORM) + psycopg 3 + Alembic migrations | Standard tooling with safe schema changes over time. |
| D6 | Config | `.env` for secrets and infrastructure (DB URL, SMTP, API key) via pydantic-settings. **User settings (channels, tags, schedules) live in Postgres** and are managed through the CLI. | Secrets stay out of the database and git. Settings can be queried and changed at runtime. |
| D7 | Interface | Typer CLI (`ann channel add @handle --tag llm`) | Quick to build. A web UI can sit on top of the same service layer later. |
| D8 | Email | stdlib `smtplib` + `email.message`, Jinja2 HTML and plain-text templates | No third-party email service needed. Gmail with an App Password works, and so does any SMTP server. Use **Mailpit** (local SMTP catcher) during development. |
| D9 | Time | Store every timestamp as `timestamptz` in UTC. Schedules keep an IANA timezone name. | Daily 08:00 stays correct across DST changes. **Install the `tzdata` package on Windows**, because `zoneinfo` has no system tz database there. |
| D10 | Delivery guarantee | At-least-once, with a DB constraint that blocks duplicates | The only possible double-send is a crash between the SMTP send and the DB commit. That's rare and harmless. |

---

## 3. System overview

```mermaid
flowchart LR
    W[app container: ann worker<br/>every 15 min<br/>or Task Scheduler without Docker] --> TICK[ann tick]
    CLI[ann CLI<br/>docker compose run app ann ...] --> SVC

    TICK --> ING[Ingest service]
    TICK --> DIG[Digest service]

    subgraph SVC[Service layer]
        ING
        DIG
        DUE[Schedule calculator<br/>pure functions]
    end

    ING --> SRC{VideoSource}
    SRC --> RSS[RSS source]
    SRC --> API[YouTube Data API<br/>optional]
    RSS -.HTTP.-> YT[(YouTube)]
    API -.HTTP.-> YT

    ING --> DB[(PostgreSQL<br/>local)]
    DIG --> DUE
    DIG --> DB
    DIG --> RENDER[Jinja2 templates]
    RENDER --> SMTP[SMTP sender]
    SMTP -.-> INBOX[Your inbox]
```

### Docker Compose topology

```mermaid
flowchart TB
    subgraph HOST[Your Windows PC: Docker Desktop]
        subgraph NET[compose network]
            MIG[migrate<br/>one-shot: ann db upgrade] --> DB
            APP[app<br/>ann worker] --> DB[(db<br/>postgres:17<br/>volume: pgdata)]
            APP --> MP[mailpit<br/>SMTP :1025, UI :8025]
            APP -. profile: ai .-> OL[ollama<br/>volume: ollama]
        end
    end
    APP -.HTTPS.-> YT[(YouTube)]
    APP -.SMTP 587.-> GM[Gmail, in prod]
    DEV[Host: uv run ann ...] -. localhost:5432 .-> DB
```

| Service | Image | Purpose | Ports on host | Volumes |
|---|---|---|---|---|
| `db` | `postgres:17-alpine` | Main database. `pg_isready` healthcheck. | `127.0.0.1:5432` | `pgdata:/var/lib/postgresql/data` |
| `migrate` | app image | Runs `ann db upgrade`, then exits. `depends_on: db (healthy)` | — | — |
| `app` | built from `Dockerfile` | `ann worker --interval 15m`. `depends_on: migrate (completed)`. `restart: unless-stopped` | — | `./logs:/app/logs`, `./out:/app/out` (dry-run emails) |
| `mailpit` | `axllent/mailpit` | Catches email during development | `127.0.0.1:8025` (UI) | — |
| `ollama` | `ollama/ollama` | Local LLM, phase 7. `profiles: [ai]` | `127.0.0.1:11434` | `ollama:/root/.ollama` |

- Bind ports to **`127.0.0.1`** so nothing is reachable from your LAN.
- **Two env files:** `.env` for running on the host (`DATABASE_URL=…@localhost:5432/…`). The compose file sets `DATABASE_URL=…@db:5432/…` for containers. Secrets come from the same `.env` through `env_file`.
- **Switching dev and prod email:** `SMTP_HOST=mailpit` / `SMTP_PORT=1025` in dev, Gmail in prod. You only change `.env`.
- **Dev workflow:** run only `db` + `mailpit` in Docker and the Python code with `uv run` on the host, which gives fast edits and a debugger. Use the full `app` container for "production" on your PC.
- **Dockerfile** (multi-stage): `python:3.14-slim` + `uv` from `ghcr.io/astral-sh/uv`. `uv sync --frozen --no-dev` into `/app/.venv` in the build stage, copy into a slim runtime stage, run as a non-root user, `ENTRYPOINT ["ann"]`.

### What one `tick` does
1. **Get a lock:** a Postgres advisory lock (`pg_try_advisory_lock`) so two ticks can't run over each other.
2. **Ingest:** for each active channel where `last_checked_at + poll_interval <= now`:
   - fetch the feed (use the stored `ETag` / `Last-Modified` so unchanged feeds come back as `304`)
   - upsert videos with `INSERT … ON CONFLICT (youtube_video_id) DO NOTHING`
   - optionally add API details to the new videos
   - update `last_checked_at`. On failure, record `last_error` and increase `consecutive_failures` for backoff.
3. **Digest:** for each active schedule where `is_due(schedule, now)`:
   - select that schedule's unsent videos
   - if there are none, update `last_run_at` and move on (unless the schedule has "send empty digest" on)
   - otherwise create a `delivery` (status `pending`) plus its `delivery_items` in one transaction → render → send → mark `sent` (or `failed` with the error)
4. **Retry:** resend `pending`/`failed` deliveries that are older than N minutes, with a retry limit.
5. Write a `runs` row with counts and errors, then release the lock.

---

## 4. Data model (PostgreSQL)

```mermaid
erDiagram
    channels ||--o{ videos : uploads
    channels ||--o{ channel_tags : has
    tags ||--o{ channel_tags : labels
    schedules ||--o{ schedule_tags : filters
    tags ||--o{ schedule_tags : "used by"
    schedules ||--o{ deliveries : produces
    deliveries ||--o{ delivery_items : contains
    videos ||--o{ delivery_items : "sent in"
    videos ||--o| video_summaries : "optional"
```

**`channels`**
- `id` PK, `youtube_channel_id` TEXT UNIQUE (`UC…`), `handle` TEXT, `title`, `uploads_playlist_id`
- `is_active` BOOL, `poll_interval_minutes` INT default 30
- `added_at` TIMESTAMPTZ. **Baseline:** videos published before this are stored but never emailed.
- `last_checked_at`, `etag`, `last_modified`, `last_error`, `consecutive_failures`

**`tags`**: `id`, `name` UNIQUE (lowercased)
**`channel_tags`**: (`channel_id`, `tag_id`) PK

**`videos`**
- `id` PK, `youtube_video_id` TEXT UNIQUE, `channel_id` FK
- `title`, `description`, `url`, `thumbnail_url`, `published_at`, `discovered_at`
- `duration_seconds` NULL, `is_short` NULL, `is_live` NULL (filled by the API)
- `raw` JSONB (source payload, for debugging and re-parsing later)
- Indexes: (`channel_id`, `published_at` DESC), (`discovered_at`)

**`schedules`** (your "settings")
- `id`, `name`, `recipient_email`, `is_active`
- `frequency` ENUM(`instant`, `hourly`, `daily`, `weekly`)
- `send_time` TIME NULL, `day_of_week` SMALLINT NULL, `timezone` TEXT (e.g. `Asia/Kolkata`)
- `send_when_empty` BOOL, `max_videos` INT NULL, `exclude_shorts` BOOL
- `created_at`, `last_run_at`
- CHECK constraints: daily needs `send_time`; weekly needs `send_time` and `day_of_week`

**`schedule_tags`**: (`schedule_id`, `tag_id`). No rows means "all channels".

**`deliveries`**: `id`, `schedule_id`, `status` ENUM(`pending`,`sent`,`failed`), `attempts`, `created_at`, `sent_at`, `error`
**`delivery_items`**: (`delivery_id`, `video_id`) plus **UNIQUE (`schedule_id`, `video_id`)**, the duplicate guard. Store `schedule_id` on the row so the constraint works.

**`runs`**: `id`, `kind`, `started_at`, `finished_at`, `channels_checked`, `new_videos`, `digests_sent`, `errors` JSONB

**`video_summaries`** (phase 7): `video_id` PK, `transcript`, `summary`, `model`, `created_at`

### The "unsent videos" query (conceptually)
```sql
SELECT v.* FROM videos v
JOIN channels c ON c.id = v.channel_id
WHERE v.published_at >= c.added_at              -- skip backlog from before you added the channel
  AND v.discovered_at >= :schedule_created_at   -- a new schedule doesn't resend history
  AND (:no_tag_filter OR v.channel_id IN (channels having any of the schedule's tags))
  AND (NOT :exclude_shorts OR v.is_short IS NOT TRUE)
  AND NOT EXISTS (SELECT 1 FROM delivery_items di
                  WHERE di.schedule_id = :sid AND di.video_id = v.id)
ORDER BY c.title, v.published_at DESC;
```

---

## 5. Project layout

```
ai_news_aggregator/
├── pyproject.toml
├── uv.lock                   # committed; Docker build uses --frozen
├── Dockerfile                # multi-stage uv build, non-root runtime
├── .dockerignore             # .venv, .git, logs, out, .env, tests cache
├── compose.yaml              # db, migrate, app, mailpit, ollama(profile: ai)
├── compose.test.yaml         # throwaway postgres on :5433 for integration tests
├── .env.example              # committed; the real .env is gitignored
├── alembic.ini
├── migrations/               # Alembic versions
├── docs/
│   └── PLAN.md
├── scripts/
│   ├── register_task.ps1     # no-Docker option: Windows Task Scheduler job
│   └── backup_db.ps1         # docker compose exec db pg_dump -> ./backups/dated file
├── src/ai_news_aggregator/
│   ├── __init__.py           # main() -> cli.app()
│   ├── cli.py                # Typer commands (thin; calls services)
│   ├── config.py             # pydantic-settings Settings
│   ├── logging.py            # log setup (rotating file + console)
│   ├── db/
│   │   ├── engine.py         # engine/session factory
│   │   ├── models.py         # SQLAlchemy ORM models
│   │   └── repositories.py   # query functions (unsent videos, due channels…)
│   ├── sources/
│   │   ├── base.py           # VideoSource Protocol, FetchedVideo dataclass
│   │   ├── rss.py
│   │   ├── youtube_api.py
│   │   └── resolver.py       # URL / @handle -> channel_id
│   ├── services/
│   │   ├── channels.py       # add/remove/tag
│   │   ├── ingest.py
│   │   ├── schedules.py      # CRUD + is_due / next_run_at (pure)
│   │   ├── digest.py         # build + deliver
│   │   └── tick.py           # lock + ingest + digest + retry
│   ├── notify/
│   │   ├── email_sender.py   # EmailSender protocol: SmtpSender, FileSender (dry-run)
│   │   └── templates/
│   │       ├── digest.html.j2
│   │       └── digest.txt.j2
│   └── summarize/            # phase 7 (optional)
└── tests/
    ├── fixtures/             # saved RSS XML, API JSON
    ├── unit/                 # parsers, is_due, rendering
    └── integration/          # against a real test Postgres DB
```

**Layer rule:** `cli` → `services` → (`db`, `sources`, `notify`). Services never import Typer. Sources and notify never touch the DB. This keeps a future web UI to a thin layer on top.

---

## 6. Dependencies

| Purpose | Package |
|---|---|
| HTTP | `httpx` |
| RSS parsing | `feedparser` (or stdlib `xml.etree`, since the feed is simple Atom) |
| ORM / driver / migrations | `sqlalchemy>=2`, `psycopg[binary]>=3`, `alembic` |
| Config | `pydantic`, `pydantic-settings` |
| CLI | `typer` (includes `rich`) |
| Templates | `jinja2` |
| Retries | `tenacity` |
| Timezones on Windows | `tzdata` |
| Dev | `pytest`, `pytest-cov`, `time-machine`, `respx`, `ruff`, `mypy` |
| Optional | `google-api-python-client` (or plain httpx for the API), `youtube-transcript-api`, `ollama` |

> Your `pyproject.toml` targets **Python 3.14**. Before Phase 0 ends, confirm that `psycopg[binary]` and every other dependency installs cleanly with `uv add`. If one doesn't, pin `.python-version` to 3.13.

---

## 7. CLI surface (v1)

```
ann db upgrade                           # alembic upgrade head
ann channel add <url|@handle|UC…> [--tag t]... [--interval 30]
ann channel list [--tag t]
ann channel remove <id|handle>
ann channel tag <channel> --add t --remove t
ann channel pause|resume <channel>

ann schedule add --name "Morning AI" --daily 08:00 --tz Asia/Kolkata --to me@x.com [--tag llm]
ann schedule add --name "Instant" --instant --to me@x.com --tag breaking
ann schedule list | remove | pause | resume
ann schedule preview <name>              # render the pending digest to an HTML file, send nothing

ann fetch [--channel c]                  # ingest only
ann digest send <name> [--dry-run]       # force a send
ann tick                                 # one run (Task Scheduler / manual)
ann worker [--interval 15m]              # loops tick forever; handles SIGTERM cleanly (container entrypoint)
ann status                               # last runs, failing channels, next due schedules
```

Inside Docker, run any command with `docker compose run --rm app <command>`,
e.g. `docker compose run --rm app channel add @AndrejKarpathy --tag llm`.
A small `ann.ps1` wrapper can shorten this to `.\ann channel add …`.

---

## 8. Configuration (`.env`)

```
POSTGRES_USER=ann             # used by the db container on first init
POSTGRES_PASSWORD=change-me
POSTGRES_DB=ai_news
DATABASE_URL=postgresql+psycopg://ann:change-me@localhost:5432/ai_news   # host; compose sets @db:5432 for containers
WORKER_INTERVAL_MINUTES=15
SMTP_HOST=smtp.gmail.com      # dev: mailpit (container) or localhost (host)
SMTP_PORT=587                 # dev: 1025
SMTP_USER=you@gmail.com
SMTP_PASSWORD=<gmail app password>
SMTP_FROM="AI News <you@gmail.com>"
YOUTUBE_API_KEY=            # optional
LOG_DIR=./logs
TICK_LOCK_ID=424242
DEFAULT_TIMEZONE=Asia/Kolkata
```

---

## 9. Execution plan (phases)

Each phase ends with something that works and has tests. Don't start the next phase until the current one's "Done when" is true.

### Phase 0: Environment and Docker base (≈1 day)
- Install Docker Desktop (WSL2 backend) and set it to start at login.
- Write `compose.yaml` with `db` (healthcheck, `pgdata` volume, `127.0.0.1:5432`) and `mailpit` (`127.0.0.1:8025`). Write `compose.test.yaml` with a throwaway Postgres on `:5433` (tmpfs, no volume).
- `uv add` the core dependencies and `uv add --dev` the dev tools. Set up ruff and mypy in `pyproject.toml`. Commit `uv.lock`.
- Create `config.py`, `.env.example`, logging setup and `alembic init migrations`.
- Write the `Dockerfile` (multi-stage, uv, non-root) and `.dockerignore`. Add the `migrate` and `app` services, with `app` just running `ann --help` for now.
- **Done when:** `docker compose up -d db mailpit` is healthy, `uv run ann --help` works on the host, `docker compose build app && docker compose run --rm app --help` works, `alembic upgrade head` runs against the container DB, and `pytest` runs.

### Phase 1: Data model and channel management (≈1–2 days)
- ORM models and the first Alembic migration for `channels`, `tags`, `channel_tags` and `videos`.
- `resolver.py`: turn `youtube.com/@handle`, `/channel/UC…` and a bare `@handle` into a channel ID. Without an API key, read the channel page's canonical link. With a key, call `channels.list?forHandle=`.
- The `ann channel …` commands.
- **Done when:** you can add, tag, list and remove your real channels, and resolver tests pass against saved HTML/JSON fixtures.

### Phase 2: Ingestion (≈1–2 days)
- The `VideoSource` protocol, `RssSource` with conditional GET (ETag), and parsing into `FetchedVideo`.
- `ingest.py`: pick the channels that are due, fetch with tenacity retries, upsert, apply the baseline rule, back off failing channels, and write a `runs` row.
- `ann fetch`.
- **Done when:** running `ann fetch` twice adds no duplicates, a channel added today shows its backlog in the DB but it's marked as baseline, one broken channel doesn't stop the others, and parser tests use saved XML.

### Phase 3: Enrichment with the YouTube API (optional, ≈1 day)
- `YouTubeApiSource.enrich(video_ids)` batches up to 50 IDs per `videos.list` call. Parse ISO-8601 durations and use `liveBroadcastContent`.
- Shorts detection: duration ≤ 60–180s is a reasonable guess. A `HEAD` request to `youtube.com/shorts/{id}` can confirm it.
- Keep a running count of quota units and log it.
- **Done when:** new videos get `duration_seconds` and `is_short`, and the app still works with no API key.

### Phase 4: Digest and email (≈2 days)
- `schedules`, `schedule_tags`, `deliveries` and `delivery_items` migration.
- Jinja2 HTML template (thumbnail, title link, channel, duration, published time in your timezone, grouped by tag or channel) plus a plain-text version.
- `EmailSender` protocol with `SmtpSender` (STARTTLS) and `FileSender` for dry runs.
- `digest.py`: unsent-videos query → pending delivery → render → send → mark sent/failed.
- `ann schedule add/list/preview` and `ann digest send --dry-run`.
- **Done when:** a digest shows up correctly in Mailpit, a second send has 0 videos, a failed SMTP send leaves a `failed` delivery that can be retried, and a real Gmail send works.

### Phase 5: Scheduling (≈1–2 days)
- `schedules.is_due(schedule, now_utc) -> bool` and `next_run_at(...)`, both pure and timezone-aware:
  - `instant`: due every tick
  - `hourly`: due when `last_run_at` was in an earlier clock hour
  - `daily`: due when local now ≥ today's `send_time` and `last_run_at` < today's `send_time`
  - `weekly`: same idea, anchored on `day_of_week`
  - Catch-up rule: a run missed while the PC was off sends **once** on the next tick, not once per missed slot.
- `tick.py` with the advisory lock, then `ann tick` and `ann status`.
- `ann worker`: a loop that runs `tick`, then sleeps until the next interval. On SIGTERM it finishes the current tick and exits (Docker sends SIGTERM on `compose stop`, so set `stop_grace_period: 60s`). One failed tick is logged and never kills the loop.
- Switch the `app` service to `worker` with `restart: unless-stopped`, and add a healthcheck that fails when `ann status --check` shows no successful run within 3× the interval.
- No-Docker option: `scripts/register_task.ps1` uses `Register-ScheduledTask` to run `uv run ann tick` every 15 min, with "run task as soon as possible after a scheduled start is missed" turned on.
- **Done when:** `time-machine` tests cover DST changes, midnight rollover, missed runs and weekly boundaries; `docker compose up -d` has run for 24h with the expected emails; and a `docker compose restart app` in the middle of a tick causes no duplicate or lost email.

### Phase 6: Hardening (≈1 day)
- Rotating file logs in `LOG_DIR`, with structured fields (channel, schedule, run_id).
- An alert email when a channel fails N times in a row, or when a tick throws.
- `scripts/backup_db.ps1`: `docker compose exec -T db pg_dump -Fc` to `./backups/ai_news_YYYYMMDD.dump`, keeping the last N. Test a restore into a scratch container.
- Container logging: stdout in JSON for `docker compose logs`, plus the rotating file in the `./logs` bind mount. Cap Docker log size (`logging: max-size: 10m`).
- A README covering setup from scratch (Docker path and no-Docker path), Gmail App Password steps, backup and restore, and troubleshooting.
- **Done when:** unplugging the network for a tick causes no crash or data loss and it recovers on the next tick; `docker compose down && docker compose up -d` keeps all data; a backup restores cleanly; and a fresh clone can be set up from the README alone.

### Phase 7: AI features (optional, ≈2–3 days)
- Fetch transcripts (`youtube-transcript-api`) for new videos and store them in `video_summaries`.
- Summarize with a local model through **Ollama** (fully local; `docker compose --profile ai up -d`, with GPU passthrough if you have an NVIDIA card) and add a 2–3 bullet summary per video to the digest. The summarizer sits behind a `Summarizer` protocol, so you could switch to a hosted LLM later.
- Keyword or topic filters per schedule.
- **Done when:** summaries show up in digests, and a missing transcript or failed summary never blocks a digest.

---

## 10. Testing strategy
- **Unit:** RSS/API parsers (fixtures), `is_due`/`next_run_at` (time-machine, many timezones), template rendering (snapshot), resolver.
- **Integration:** a real Postgres from `compose.test.yaml` (port 5433, tmpfs, same major version as prod). A session fixture runs Alembic migrations, and each test runs in a transaction that is rolled back. Cover ingest idempotency, the unsent-videos query, the delivery lifecycle and the tick lock.
- **HTTP:** `respx` mocks. Tests never call real YouTube.
- **End-to-end smoke:** `ann tick` against Mailpit using a fake source.

---

## 11. Risks and how they're handled

| Risk | Mitigation |
|---|---|
| YouTube changes or throttles RSS | The source abstraction lets you fall back to the API. Backoff and `consecutive_failures` alerts. |
| API quota exhausted | The API is only used for extra detail in batches of 50. Never use `search.list`. The quota counter is logged. |
| Email flood when adding a channel or schedule | Baseline rules (`published_at >= channel.added_at`, `discovered_at >= schedule.created_at`) |
| Duplicate emails | UNIQUE (`schedule_id`, `video_id`) plus the advisory lock |
| PC asleep at send time | The worker (or Task Scheduler's "run missed task" setting) runs on wake, plus a single catch-up send |
| Docker Desktop not running after reboot | Turn on "Start Docker Desktop when you sign in". `restart: unless-stopped` on every service. The `ann status` healthcheck. |
| Losing the DB volume (`docker compose down -v`, Docker reset) | Named volume + scheduled `pg_dump` backups to a host folder. Never run `down -v` on prod. |
| Container clock and timezone | Everything is computed in UTC with `zoneinfo`/`tzdata`, so the container's TZ doesn't matter |
| DB reachable from your network | Every port binds to `127.0.0.1`. Postgres password lives in `.env`. |
| Gmail blocks SMTP login | Use an App Password (needs 2FA). The sender can be swapped for any SMTP server. |
| Windows timezone bugs | The `tzdata` dependency, UTC storage, and DST tests |
| Python 3.14 wheel gaps | Check in Phase 0 and fall back to 3.13 |

---

## 12. Open questions (defaults assumed in this plan)
1. **YouTube API key:** will you create one? *Default: optional. v1 works on RSS alone.*
2. **Email provider:** Gmail? *Default: Gmail SMTP with an App Password.*
3. **Where the app runs:** does the worker run in Docker too, or only Postgres and Mailpit? *Default: full stack in Docker for everyday use, with `uv run` on the host while developing.*
4. **Summaries:** do you want AI summaries in v1 or later? *Default: later (Phase 7), with Ollama.*
5. **Single recipient:** is it only you? *Default: yes, though each schedule stores its own recipient so this is easy to extend.*
