# clipfinder — local → multi-user migration

Turn clipfinder from a single-user localhost tool into a **self-hosted, open-signup, free**
web app on **one VPS via Docker Compose**, on a **shared server-side Groq key** (owner pays,
per-user quotas protect the bill), with a **React + Vite + TypeScript** frontend.

> **Capacity fix — 2026-09-11: `FFMPEG_THREADS`.** Every `ffmpeg` call in `clipfinder.py`
> auto-detected and grabbed every core by default; under `WORKER_CONCURRENCY > 1` this meant
> concurrent jobs fought over the same cores and render time became a function of how many
> other users happened to be active — inconsistent per-user experience, not a capacity number
> anyone could reason about. Fixed with the same module-level-hook pattern as `GROQ_GATE`:
> `clipfinder.FFMPEG_THREADS` (default `0` = unchanged single-instance/CLI behaviour) is read
> from `Settings.ffmpeg_threads` and set once in `app/worker.py` `on_startup` and
> `app/main.py`'s lifespan; `_thread_args()` is spliced into every real ffmpeg invocation
> (`extract_audio`, `cut_clip`, `mix_sfx`, `grab_frame`, `loudness_track`, and the web-side
> waveform/frame endpoints in `app/projects.py`). Operators size
> `WORKER_CONCURRENCY × FFMPEG_THREADS ≈ vCPU count − 1` (worked examples in `DEPLOY.md`
> §7); a commented `deploy.resources.limits.cpus` guard was added on `worker` in
> `compose.yaml` as a hard backstop. Verified: `on_startup` sets `clipfinder.FFMPEG_THREADS`
> correctly end-to-end; a full render (including the most complex `cut_clip` path — 9:16
> `cropfill` with a blurred backdrop + subject crop + overlay filter-graph) completed cleanly
> with `-threads 2` active and downloaded successfully. Also removed the stale pre-migration
> `clipfinder/server.py` (superseded by `app/main.py` back in Phase 1, never deleted).
> **Unrelated finding while testing this:** the dev Groq key in `.env` has expired
> (`expired_api_key` from Groq) after the heavy test load across this session — needs a
> fresh key before any *real* (non-seeded) analyze will work again; doesn't affect the fix
> above, which was verified via the render path (no Groq call) and direct `on_startup` checks.

> **Full-stack debug pass — 2026-09-04 (post Phase 8).** Wiped all volumes and re-migrated
> from empty (`3b8b...` → `6de7...` → `2d1d...` clean in sequence) to rule out drift, then ran
> a scripted, cookie-real audit (register/verify/login, route guards, CSRF, resumable tus
> upload, analyze, editor autosave, per-clip render with crop/caption/color/sfx, SFX
> isolation, cross-user isolation, path traversal, quotas, admin, retention warn→delete,
> backup) plus a full browser click-through (register → verify → login → create → editor →
> render → download) through the real React SPA. **48/48 + 15/15 + 11/11 checks passed.**
> **One real bug found and fixed:** `GET /api/projects/{id}` could report a stale
> `project.status` next to a fresh `job.status` — `_owned()` loads the project, commits an
> unrelated `last_opened_at` touch, and (`expire_on_commit=False`) never re-reads `status`
> before the response is built; if the worker's commit landed in that gap, the two fields
> could momentarily disagree (harmless-looking but wrong data). Fixed with a targeted
> `session.refresh(p, attribute_names=["status","duration_seconds","error"])` right before
> the response, closing the window to microseconds. See `app/projects.py` `get_project`.
> Everything else held: security boundaries (401/403/404 on every guarded and cross-user
> path), CSRF, the tus resumable-upload authz hook, per-clip render fidelity (9:16 crop,
> captions, color, SFX mixing all reproduced from the editor state), quota enforcement,
> admin, retention, and the SPA itself (0 JS errors, 0 horizontal overflow) — all clean.

## Decisions locked (from the brief)

| Question | Answer | Consequence |
|---|---|---|
| Audience | Open public signup, free | Email verification required, rate-limits, per-user quotas, retention/GC, abuse controls |
| Hosting | Single VPS + Docker Compose (self-managed) | Everything in one `compose.yaml`: web + worker + Postgres + Redis + Caddy + tusd. Local disk for media. |
| API cost | One shared Groq key on the server | Global Groq token-bucket + per-user monthly minute cap. No per-user keys in v1. |
| Frontend | Move to framework + build | Full React/Vite/TS SPA. Port `web/app.js` editor logic component by component. |

**Claude / mixed-model / budget-cap features stay in the codebase but dormant** — Groq-only in
v1, no Settings page for keys or models. The `verify_model` / `claude_budget` machinery added
earlier remains admin-configurable via env, unused by default.

---

## What survives essentially unchanged

- **`clipfinder.py`** — the engine. ffmpeg helpers, `transcribe()`, `find_clips()` /
  `_find_clips_groq` / `_verify_and_refine`, `_finalize`, `loudness_track`, `_auto_edit`,
  `cut_clip`, `mix_sfx`, ASS/caption logic, `slugify`. No changes except: `load_dotenv` /
  `DEFAULTS` sourcing moves to env (Phase 1), and paths it's handed are per-user (Phase 3).
- **`pipeline.py`** — `analyze()`, `render()`, `build_ass()`, `caption_lines()`. Already takes
  `cfg` + `on_progress` / `on_clip` / `on_stage` callbacks. Only `get_cfg()` changes (env, not
  `_settings.json`) and the callbacks write to Postgres instead of an in-memory dict.
- The **ffmpeg SFX synthesis** (`_SFX_SYNTH` in server.py) — moves to a worker startup task,
  writes builtin SFX once to the shared media volume.
- The **design language** — tokens in `styles.css`, the Liquid Glass background, violet/cyan,
  chrome wordmark, Space Grotesk. Ported into the SPA, not redesigned. Fonts self-hosted
  (drop the `fonts.googleapis.com` `@import`).

## What is removed

- `GET /api/browse` + `POST /api/add` — scanning the server's local filesystem for videos makes
  no sense multi-user. Upload only.
- `_source_dirs()`, `BROWSE_SKIP_DIRS`, `clipfinder.toml [source]`.
- The **Settings page** (`/api/settings`, `_write_env`, `_mask_key`, `_load_settings`,
  `_settings.json`) — no user-editable keys or models. A stripped admin-only config may return
  later; not in this plan.
- In-memory `JOBS` dict, `_get_job`'s "restored from disk" rebuild, `BackgroundTasks` for
  analyze/render.
- The job-id-as-directory scheme (`web_data/<job_id>/`) — replaced by
  `media/users/<user_id>/projects/<project_id>/`.

## What is renamed

"job" was overloaded (an upload + its analysis + its edits + its renders). Split into:
- **project** — one uploaded video and everything derived from it (the durable entity).
- **job** — one queued unit of async work (`analyze` or `render`) against a project.

---

## Target architecture

```
                          ┌──────────────────────────── VPS ────────────────────────────┐
   Browser ──HTTPS──▶  caddy  ──/──────────▶  SPA static files (built, baked into image) │
                          │    ──/api/*─────▶  web        (FastAPI, uvicorn, async)       │
                          │    ──/files/*───▶  tusd       (resumable upload, Go)          │
                          │                      │  hooks: POST /api/upload/hooks         │
                          │                      ▼                                        │
                          │                   web ──────▶ postgres  (projects/jobs/usage) │
                          │                     │  ├─────▶ redis     (arq queue + limits)  │
                          │                     ▼                                          │
                          │                  worker(s)  (arq)  ── ffmpeg + Groq API ──▶ internet
                          │                     │                                          │
                          │  shared volume `media/`  ◀── web (serve) · worker (write) · tusd (stage)
                          └─────────────────────────────────────────────────────────────────┘
```

**compose services:** `caddy`, `web`, `worker`, `postgres`, `redis`, `tusd`
(+ `mailhog` in the dev override only).
**named volumes:** `pgdata`, `media`, `caddy_data`, `caddy_config`, `redis_data`.

### Media volume layout

```
media/
  users/<user_id>/projects/<project_id>/
      source.<ext>
      clips/<nn>_<slug>[_vertical|_fill|_subject][_cc].mp4
      frames/<sec>.jpg
      waveform.png
  sfx/_builtin/<name>.m4a
  sfx/users/<user_id>/<slug>.<ext>
  _uploads/            # tusd staging; finalized files are moved out into a project dir
```
`analysis` (segments + clips JSON) and the editor `project_edits` state live in **Postgres
JSONB**, not on disk.

---

## Data model (Postgres 18, SQLAlchemy 2.0 async, Alembic)

All ids are `uuid` (v4, app-generated so enqueue is retriable). All tables `created_at`,
most `updated_at` (server_default `now()`).

### `users`  — schema from fastapi-users' SQLAlchemy base, plus columns
| column | type | notes |
|---|---|---|
| id | uuid pk | |
| email | citext unique | |
| hashed_password | text | Argon2id (pwdlib) |
| is_active | bool default true | |
| is_verified | bool default false | gate all jobs on this |
| is_superuser | bool default false | admin |
| display_name | text null | |
| quota_minutes_override | int null | manual per-user bump; null = plan default |
| quota_storage_mb_override | int null | |
| created_at | timestamptz | |

### `projects`
| column | type | notes |
|---|---|---|
| id | uuid pk | |
| user_id | uuid fk users on delete cascade, indexed | |
| name | text | from original filename, editable |
| source_filename | text | |
| source_ext | text | `.mp4` … |
| source_bytes | bigint null | set on upload finalize |
| duration_seconds | float null | set by analyze |
| status | text | `uploading` / `queued` / `analyzing` / `ready` / `failed` |
| error | text null | last analyze failure |
| last_opened_at | timestamptz null | drives retention |
| created_at / updated_at | timestamptz | |

### `analyses`  — one row per project (unique `project_id`), replaced on re-analyze
| column | type | notes |
|---|---|---|
| id | uuid pk | |
| project_id | uuid fk unique on delete cascade | |
| segments | jsonb | Whisper segments (+ words) |
| clips | jsonb | ranked `Clip.model_dump()` list, incl. `auto` |
| spend | jsonb null | `{usd, cap, models}` when Claude ran (dormant in v1) |
| backend | text | `groq` |
| created_at | timestamptz | |

### `project_edits`  — one row per project (unique), the SPA's autosave target
| column | type | notes |
|---|---|---|
| project_id | uuid fk pk on delete cascade | |
| state | jsonb | today's `project.json` payload verbatim (queue, kept, cap{…anim}, sfxAdds, sfxAuto, autoApplied, autoOff, color, lastClip) |
| updated_at | timestamptz | |

### `jobs`
| column | type | notes |
|---|---|---|
| id | uuid pk | |
| user_id | uuid fk indexed | denormalized for quota queries |
| project_id | uuid fk on delete cascade indexed | |
| kind | text | `analyze` / `render` |
| status | text | `queued` / `running` / `done` / `error` |
| progress_pct | int default 0 | |
| progress_msg | text default '' | |
| stage | text default '' | render: "Rendering clip 2 of 5" |
| error | text null | |
| arq_job_id | text null | for cancel / dedup |
| payload | jsonb null | render: `{ranges:[…]}` |
| result | jsonb null | analyze: `{duration}`; render: `{clips:[…]}` |
| created_at / started_at / finished_at | timestamptz | |

Index `(project_id, kind, status)` and `(status, created_at)` (queue drain / orphan sweep).

### `render_outputs`
| column | type | notes |
|---|---|---|
| id | uuid pk | |
| project_id | uuid fk on delete cascade indexed | |
| job_id | uuid fk null | |
| filename | text | file on disk under `…/clips/` |
| duration | float | |
| vertical | bool | |
| captions | bool | |
| sfx_count | int | |
| bytes | bigint | |
| created_at | timestamptz | |

### `sfx_assets`
| column | type | notes |
|---|---|---|
| id | uuid pk | |
| user_id | uuid fk null | null = builtin (shared) |
| filename | text | on disk |
| name | text | display |
| bytes | int | |
| created_at | timestamptz | |
Unique `(user_id, filename)`.

### `usage_events`  — append-only meter
| column | type | notes |
|---|---|---|
| id | uuid pk | |
| user_id | uuid fk indexed | |
| project_id | uuid fk null on delete set null | |
| kind | text | `transcribe_seconds` / `render_clip` / `upload_bytes` |
| quantity | bigint | seconds, count, or bytes |
| created_at | timestamptz indexed | window queries by month |

Monthly usage = `SUM(quantity) WHERE user_id=? AND kind='transcribe_seconds' AND created_at >= date_trunc('month', now())`.
Storage used = `SUM(source_bytes) + SUM(render_outputs.bytes)` per user (or a maintained
counter; start with the query).

**Email verification / password reset tokens** — fastapi-users issues stateless signed JWTs;
no table.
**Sessions** — JWT access token (short) in an **httpOnly cookie** + a refresh token cookie;
no server session table in v1 (revocation = rotate the signing secret / short TTL). A
`refresh_tokens` table is a later hardening step, not this plan.

---

## Cross-cutting policy

### Auth transport — **cookies, not bearer headers**
The SPA needs `<video src>`, `<img src>`, and `EventSource` to be authenticated, none of
which can send an `Authorization` header. Use fastapi-users' **CookieTransport** (httpOnly,
`Secure`, `SameSite=Lax`) for the access token + a refresh cookie. Add **CSRF protection**
(double-submit token in a non-httpOnly cookie, checked on every unsafe method) because cookies
auto-send. `SameSite=Lax` already blocks cross-site POST; the double-submit check is defense
in depth.

### Quotas (free plan defaults — all env-tunable)
| limit | default | enforced at |
|---|---|---|
| transcription minutes / month | 60 | analyze enqueue |
| max source file | 2 GB | tusd pre-create hook + upload finalize |
| total stored media / user | 5 GB | tusd pre-create hook |
| renders / day | 40 | render enqueue |
| concurrent jobs / user | 1 | any enqueue |
| projects / user | 25 | project create |
| email must be verified | — | every enqueue + project create |

Over-limit → `429` + JSON `{error, limit, used, resets_at}`. The SPA Account page shows the
same numbers.

### Protecting the shared Groq key
A **global Redis token-bucket** sized to the Groq free tier (≈ 8k tokens/min, plus a daily
ceiling). `transcribe()` and `find_clips()` acquire from it before each call; on empty, the
worker sleeps and retries (respecting `retry-after`, as `_groq_chat` already does). Plus a
**global queue-depth guard**: if `arq` queued+running analyze jobs exceed N, `POST` returns
`503 busy` instead of enqueuing.

### Retention / GC (open free tier fills disk fast)
arq **cron** task, daily: projects with `last_opened_at` (or `created_at`) older than **30
days** → email a 7-day warning; at 37 days → delete DB rows (cascade) + `rm -rf` the project
media dir. Env-tunable, off by setting to 0.

### SSE across workers
`web` runs multiple uvicorn workers; job state is in Postgres. SSE endpoints **poll the
`jobs` row every 0.5 s** and emit on change — same wire format as today's endpoints. (Redis
pub/sub is a later optimization; polling one indexed row is fine at this scale.)

---

# Phase 0 — Documentation Discovery  ✅ done (consolidated below)

## Allowed APIs & pinned versions

### Runtime / infra
- **Python** 3.12 (match the current venv). **FastAPI** 0.141.x (already installed).
  **Starlette** 1.6.x, **uvicorn** 0.52.x, **pydantic** 2.13.x — all already present.
- **PostgreSQL 18**. **SQLAlchemy 2.0.44** with `sqlalchemy[asyncio]`, **asyncpg 0.31.0**,
  **Alembic 1.17.1**.  Install: `pip install "sqlalchemy[asyncio]" asyncpg alembic`.
  Source: SQLAlchemy 2.0 async docs; the 2026 "production-grade async backend" guides below.
- **Redis 7** (container `redis:7-alpine`).
- **arq** (async Redis task queue, by the pydantic author — built for asyncio/FastAPI, Redis
  is the only backend, worker runs in the event loop). This is the recommended default for a
  fresh async FastAPI project in 2026 over Celery/Dramatiq unless workflow primitives are
  needed — they are not here.  `pip install arq`.
- **Caddy 2.x** (pin e.g. `caddy:2.11-alpine`, never `:latest`). Automatic HTTPS via
  Let's Encrypt; Caddyfile reverse-proxy syntax; **persist `/data`** (named volume) or
  Let's Encrypt will rate-limit/ban on repeated cert requests.
- **tusd** (`tusio/tusd` image) for resumable uploads; `-hooks-http <url>` forwards the
  client `Authorization`/cookies to an API endpoint for authz; `-upload-dir`. Frontend:
  **Uppy** with the Tus plugin, or `tus-js-client`.

### Auth
- **fastapi-users 15.0.5** (March 2026, MIT, *maintenance mode* — stable, still gets security
  + dependency updates; we need no new features). Provides ready routers for
  **register / login / logout / verify-email / forgot-password / reset-password** and a
  `current_user` dependency family. `pip install "fastapi-users[sqlalchemy]"`.
  - Adapter: `fastapi_users_db_sqlalchemy.SQLAlchemyUserDatabase`.
  - Strategy: `JWTStrategy(secret=…, lifetime_seconds=…)`; Transport:
    `CookieTransport(cookie_name="cfa", cookie_max_age=…, cookie_secure=True, cookie_httponly=True, cookie_samesite="lax")`.
  - `UserManager.on_after_register` / `on_after_forgot_password` / `on_after_request_verify`
    send the emails.
- **Password hashing: pwdlib with Argon2id** — the current FastAPI-recommended hasher
  (`passlib` is now lightly maintained; `python-jose` is stale). fastapi-users 15 already
  uses pwdlib internally. Don't add `passlib` or `python-jose`.
- **JWT lib**: **PyJWT** if any manual token work is needed (fastapi-users bundles its own).
  Do NOT use `python-jose`.
- **Rate limiting**: **slowapi** (Starlette/​FastAPI limiter, Redis storage) on
  `/api/auth/*`. Or enforce at Caddy. Use slowapi for per-user/per-route granularity.
- **Email**: a transactional provider over SMTP or HTTP API — Resend / Postmark / Brevo /
  Amazon SES. In code, one `send_email(to, subject, html)` abstraction; dev uses **MailHog**
  (SMTP catch-all container, web UI on :8025). Do not hard-code a provider SDK; SMTP via
  `aiosmtplib` keeps it swappable.
- **pydantic-settings** for config (`pip install pydantic-settings`) — all secrets/limits as
  env vars, one `Settings` class.

### Anti-patterns to avoid (verified against current docs)
- ❌ `from passlib.context import CryptContext` / `python-jose` — both superseded; use pwdlib
  (via fastapi-users) + PyJWT.
- ❌ `Base = declarative_base()` (1.x style) — SQLAlchemy 2.0 uses
  `class Base(DeclarativeBase): pass` and `Mapped[...]` / `mapped_column()`.
- ❌ synchronous `create_engine` + `Session` in async routes — use
  `create_async_engine` + `async_sessionmaker` + an `async def get_session()` dependency.
- ❌ Celery + `BackgroundTasks` for the heavy work — arq worker service.
- ❌ Alembic autogenerate without setting `target_metadata` and `compare_type=True` /
  `render_as_batch` off for Postgres.
- ❌ Caddy `:latest`, or not persisting `caddy_data`.
- ❌ Storing the access token in `localStorage` — httpOnly cookie only.
- ❌ tusd writing straight into a project dir — it stages to `_uploads/`, the finish hook
  moves the file (atomic `rename` on the same volume).
- ❌ SSE holding a DB transaction open for the whole stream — open a short session per poll.

## Existing patterns to copy (in this repo)
- `pipeline.analyze(video_path, cfg, on_progress)` and
  `pipeline.render(video_path, ranges, cfg, out_dir, segments, sfx_dir, on_clip, on_stage)` —
  call these unchanged from the worker; wire the callbacks to `jobs`-row updates.
- The SSE generator shape in `server.py:246-264` / `304-323` — keep the `data: {json}\n\n`
  format and the `{status,pct,msg,error}` / `{status,done,total,current,error,clips}` payloads
  so the SPA's `EventSource` code ports 1:1.
- `pipeline.get_cfg()` (`pipeline.py:19`) — same return dict, but built from `Settings` env
  defaults instead of `clipfinder.toml` + `_settings.json`.
- Clip/edit JSON shapes: `analysis.json` = `{duration, segments, clips}`;
  `project.json` = the `projectState()` object in `web/app.js`. These become the `analyses`
  and `project_edits` JSONB columns unchanged.

Sources: see bottom of file.

---

# Phase 1 — Backend foundation: config, DB, Docker Compose skeleton

**Goal:** the current single-user app runs inside Docker Compose against Postgres + Redis,
with Alembic migrations and env config. **No auth, no behavior change yet.**

> **✅ DONE (2026-09-04).** Files: `pyproject.toml` + `requirements.txt` (pinned),
> `app/{config,db,main,legacy}.py`, `app/models/{__init__,user}.py`, `alembic/` (async env,
> `compare_type`+`compare_server_default`), `alembic/versions/3b8b81585e0a_create_users.py`
> (hand-fixed: `CREATE EXTENSION citext` + `postgresql.UUID` instead of the unimported
> `GUID`), `Dockerfile`, `compose.yaml` + `compose.override.yaml` (dev: bind-mount + reload +
> Mailpit), `Caddyfile`, `.env.example`, `.dockerignore`.
> Deviations from the plan text below: (a) kept `pipeline.get_cfg()` reading
> `clipfinder.toml` + `_settings.json` unchanged — "no behavior change" beats moving engine
> config to env now; that happens with the Settings-page removal in Phase 3. (b) `server.py`
> became `app/legacy.py` as an `APIRouter` (not deleted) — every old route still served.
> (c) Postgres 18 image needs the volume at `/var/lib/postgresql` (not `/data`). (d) Mailpit,
> not the unmaintained MailHog. (e) FastAPI 0.141 uses lazy `_IncludedRouter` — routes
> resolve at request time, `app.routes` looks short at import; fine.
> Verified: `docker compose up -d` → all 5 services healthy; `curl :8000/api/health` →
> `{"ok":true,"db":true,"redis":true}` through Caddy; `alembic upgrade head` creates `users`,
> `downgrade -1`/`upgrade head` round-trips clean; autogenerate on no-op → empty; `/`,
> `/api/recent`, `/api/sfx` (ffmpeg SFX synth in-container) all 200. Full analyze/render not
> re-run (Groq quota) — that code path is byte-identical to the working single-user app.
> Stack left running for Phase 2.

### What to implement
1. `pyproject.toml` (or `requirements.txt`) with every dep **pinned**: current venv set +
   `sqlalchemy[asyncio]==2.0.44`, `asyncpg==0.31.0`, `alembic==1.17.1`, `arq`,
   `fastapi-users[sqlalchemy]==15.0.5`, `pydantic-settings`, `slowapi`, `aiosmtplib`.
2. `app/config.py` — `class Settings(BaseSettings)` (pydantic-settings): `database_url`,
   `redis_url`, `groq_api_key`, `anthropic_api_key` (optional), `jwt_secret`,
   `cookie_secure`, `media_root`, `public_base_url`, `smtp_*`, all the quota ints, retention
   days. `.env.example` documents them.
3. `app/db.py` — copy the SQLAlchemy 2.0 async pattern: `create_async_engine(settings.database_url, pool_pre_ping=True)`,
   `async_sessionmaker(expire_on_commit=False)`, `async def get_session() -> AsyncIterator[AsyncSession]`.
   `class Base(DeclarativeBase)`.
4. `app/models/user.py` — `User(SQLAlchemyBaseUserTableUUID, Base)` (fastapi-users base only;
   no auth wiring yet).
5. `alembic/` — `alembic init -t async alembic`; set `target_metadata = Base.metadata`,
   `compare_type=True`; env reads `settings.database_url`. First revision: `users` table.
6. Move `server.py` into `app/` as `app/main.py`; keep every existing route working
   (still filesystem `web_data/`, still `JOBS` dict, still `BackgroundTasks`). Add
   `GET /api/health` → `{ok, db, redis}` (pings both).
7. `pipeline.get_cfg()` → build from `Settings` (bake `clipfinder.toml`'s current values as
   the defaults). Delete the `_settings.json` overlay. `clipfinder.load_dotenv` call removed
   (env comes from the container).
8. `Dockerfile` — python:3.12-slim, install ffmpeg (`apt-get install -y ffmpeg`), pip install,
   copy `app/ clipfinder.py pipeline.py`. `Dockerfile.web` also copies the built SPA later.
9. `compose.yaml` — `postgres:18`, `redis:7-alpine`, `web` (uvicorn, `depends_on` db+redis
   healthchecks), `caddy:2.11-alpine` (reverse-proxy `/api/*` → web, serve `web/` static for
   now). `compose.override.yaml` (dev): bind-mount source, `--reload`, MailHog, expose ports.
10. `Caddyfile` — `:80` (dev) / `{$DOMAIN}` (prod), `reverse_proxy /api/* web:8000`,
    `handle /* { root * /srv/web; file_server }`, `request_body { max_size 2GB }`. Persist
    `caddy_data:/data`.

### Verification checklist
- `docker compose up -d` → all services healthy; `curl :80/api/health` → `{"ok":true,"db":true,"redis":true}`.
- `docker compose exec web alembic upgrade head` creates `users`; `alembic downgrade -1` clean.
- `docker compose exec web alembic revision --autogenerate` on a no-op change → empty migration
  (metadata matches DB).
- The current flow still works through the container: open `:80`, upload a short video,
  analyze completes, edit + render a clip, download it.
- `docker compose exec web python -c "import clipfinder, pipeline"` — no import errors.

### Anti-pattern guards
- No `declarative_base()`, no sync `Session` in async paths.
- No secret literals in code or `compose.yaml` — only `${VAR}` from `.env`.
- Don't wire fastapi-users routers yet (Phase 2).
- Alembic `env.py` must import the models package so `Base.metadata` is populated.

---

# Phase 2 — Auth: accounts, email verification, password reset

**Goal:** a user can register, verify by email, log in (cookie), reset a password. Protected
routes reject anonymous / unverified.

> **✅ DONE (2026-09-04).** Files: `app/auth/{__init__,schemas,manager,backend,routes}.py`,
> `app/email.py` (aiosmtplib, HTML shell + verify/reset templates, `send_*` swallow SMTP
> errors so registration never 500s), `app/ratelimit.py`, `app/csrf.py`. `app/main.py` adds
> `CSRFMiddleware` + `install_auth(app)`.
> Stack: fastapi-users 15.0.5, `CookieTransport(cookie_name="cfa", httponly, samesite=lax,
> max_age=3600)` + `JWTStrategy(jwt_secret)`. Routers: `/api/auth/{register,login,logout,
> verify,request-verify-token,forgot-password,reset-password}` + `/api/users/{me,{id}}`,
> `requires_verification=True` on login and the users router. `on_after_register` →
> `request_verify` → `on_after_request_verify` → verify email.
> Deviations: (a) **hand-rolled Redis rate-limit dependency** instead of slowapi — slowapi's
> decorator model doesn't compose with third-party routers; applied as
> `dependencies=[Depends(rate_limit(...))]` on the auth router includes (register 5/h,
> login 10/15m, verify 10/h, reset 5/h). slowapi stays in requirements for Phase 5's global
> limit. (b) No new migration — the Phase 1 `users` table already has every fastapi-users
> column. (c) email column is CITEXT; fastapi-users' `func.lower` lookups still work.
> Verified end-to-end against the Docker stack + Mailpit:
> register→201 (is_verified false) · verify email lands in Mailpit · POST /verify→200
> (is_verified true) · login→204 `Set-Cookie: cfa=…; HttpOnly; SameSite=lax` + csrftoken ·
> `/api/users/me` with cookie→200, without→401 · unverified login→400
> `LOGIN_USER_NOT_VERIFIED` · PATCH `/api/users/me` no `X-CSRF-Token`→403, with→200 ·
> forgot→reset email→reset-password 200, new pw logs in / old pw 400 · 6th register from an
> IP→429 · logout→204 `cfa=""; Max-Age=0`, `/me`→401. Anti-pattern grep of `app/` (minus
> legacy.py): clean. Image rebuilt.

### What to implement
1. `app/auth/` — copy fastapi-users' **full SQLAlchemy example** (docs:
   `fastapi-users.github.io/fastapi-users/latest/configuration/full-example/`):
   - `get_user_db` (SQLAlchemyUserDatabase), `UserManager(UUIDIDMixin, BaseUserManager)` with
     `reset_password_token_secret` / `verification_token_secret` from `settings`.
   - `on_after_register` → send verify email; `on_after_forgot_password` → send reset email;
     `on_after_request_verify` → send verify email.
   - `AuthenticationBackend("cookie", transport=CookieTransport(...), get_strategy=JWTStrategy)`.
   - `fastapi_users = FastAPIUsers[User, uuid.UUID](get_user_manager, [cookie_backend])`.
2. Mount routers under `/api/auth`:
   `get_auth_router`, `get_register_router`, `get_verify_router`, `get_reset_password_router`,
   and `get_users_router` under `/api/users`.
3. `app/auth/deps.py` — export `current_user = fastapi_users.current_user(active=True)` and
   `current_verified_user = fastapi_users.current_user(active=True, verified=True)`.
4. `app/email.py` — `async def send_email(to, subject, html)` via `aiosmtplib` +
   `settings.smtp_*`. Two templates (verify, reset) with `{link}` =
   `{public_base_url}/verify?token=…` / `/reset?token=…` (SPA routes).
5. **CSRF** — middleware: on `POST/PUT/PATCH/DELETE` under `/api`, require header
   `X-CSRF-Token` to equal the `csrftoken` cookie (set on any GET that has no cookie). Exempt
   `/api/auth/login` (pre-cookie) — it's `SameSite=Lax` + form POST, acceptable.
6. **Rate-limit** with slowapi (Redis storage): `/api/auth/register` 5/hour/IP,
   `/api/auth/forgot-password` 5/hour/IP, `/api/auth/login` 10/15min/IP.
7. `compose.override.yaml` adds **mailhog** (`axllent/mailhog`), `smtp_host=mailhog`.

### Verification checklist
- Register → 201; MailHog shows a verify email; open link → `POST /api/auth/verify` → user
  `is_verified=true`.
- Login → `Set-Cookie: cfa=…; HttpOnly; SameSite=Lax`; `GET /api/users/me` → 200 with email.
- `GET` a route guarded by `current_verified_user` while unverified → 403; verified → 200.
- Forgot-password → email → reset with token → old password fails, new works.
- `POST /api/users/me` without `X-CSRF-Token` → 403; with matching token → 200.
- 6th register in an hour from one IP → 429.
- Logout clears the cookie; `GET /api/users/me` → 401.

### Anti-pattern guards
- Cookie must be `HttpOnly` + `Secure` (prod) + `SameSite=Lax`. Never return the JWT in a body.
- Don't invent fastapi-users APIs — copy the pinned-version example verbatim; check method
  names against `fastapi-users==15.0.5` source if unsure.
- `verification_token_secret` ≠ `jwt_secret` ≠ `reset_password_token_secret` (3 distinct env
  values) so leaking one scope doesn't grant another.
- No login without `is_verified` — set `require_verification=True` on the auth router.

---

# Phase 3 — Multi-tenant data model + per-user storage + user-scoped routes

**Goal:** every project belongs to a user; users cannot see or fetch each other's data;
media lives under `users/<id>/`. Analyze/render still run via `BackgroundTasks` (Phase 4
swaps the queue) — this phase is purely the data/route reshape.

> **✅ DONE (2026-09-04).** Files: `app/models/{project,job,sfx,usage}.py` (7 tables),
> `app/storage.py` (every media path, traversal-guarded), `app/projects.py`
> (project/job/edit/render/media routes), `app/sfx.py` (builtin + per-user), `app/jobs.py`
> (`run_analyze` / `run_render` — `asyncio.to_thread(pipeline.*)` + a `call_soon_threadsafe`
> queue that a drain task writes to the `jobs` row). `app/legacy.py` **deleted**;
> `app/main.py` includes the new routers only. `pipeline.get_cfg()` dropped the
> `_settings.json` overlay. Migration `6de74cecf724` (autogen; hand-fix #1: every FK column
> needs an explicit `UUID(as_uuid=True)` or autogen emits the unimported
> `fastapi_users_db_sqlalchemy.generics.GUID` — fixed in the models, not the migration).
> Removed routes: `/api/browse`, `/api/add`, `/api/recent`, `/api/jobs/*`, `/api/settings`,
> `/api/upload`. New: `/api/projects[...]`, `/media/{project_id}/*`, `/media/sfx/{asset_id}`.
> Deviations: (a) `BackgroundTasks` stays (plan says so — Phase 4 removes it); the executor +
> queue-drain in `jobs.py` is already the Phase 4 worker body. (b) analysis segments/clips →
> `analyses` JSONB; editor state → `project_edits` JSONB; only binaries on disk. (c) render
> stages every referenced sfx (builtin or user's) into `<project>/_sfxstage/` for the
> renderer, cleaned up after. (d) `/media/_sfx/` from the pre-Phase-3 legacy path is a
> harmless orphan dir.
> Verified end-to-end (Docker + Mailpit + a real 22 s test clip, GROQ live): register/verify
> 2 users → alice create project (200) → upload source (200, job created) → SSE analyze
> `analyzing→ready`, job `running 65%→done 100%`, `analyses` row written (6 segments,
> duration 22; 0 clips — the short mid-convo clip yielded none, plumbing correct) → edit
> save/get round-trips the exact JSONB → render 1 range → `render_outputs` row +
> `01_test.mp4` (7 s) → `GET /media/<pid>/clips/…` 200 (705 KB). **Isolation:** bob's project
> list empty; bob GET/DELETE alice's project → 404; bob GET alice's `/media` → 404. Path
> traversal `..%2f..%2fsource.mp4` → 404. SFX: 5 builtins seeded + shared; cara uploads one →
> cara own=1, dave own=0, eve GET cara's sfx id → 404, eve GET builtin → 200. Media on disk:
> `media/users/<user_id>/projects/<project_id>/source.mp4`. Image rebuilt; head
> `6de74cecf724`.

### What to implement
1. Models + Alembic revision: `projects`, `analyses`, `project_edits`, `jobs`,
   `render_outputs`, `sfx_assets`, `usage_events` (schema above).
2. `app/storage.py` — the **only** place paths are built:
   `project_dir(user_id, project_id) -> Path`, `clips_dir(...)`, `frames_dir(...)`,
   `source_path(project, ...)`, `user_sfx_dir(user_id)`, `builtin_sfx_dir()`. Creates dirs.
   `assert` the resolved path is inside `settings.media_root` (path-traversal guard).
3. Rewrite routes, all under `Depends(current_verified_user)`, all filtering by `user.id`:
   | old | new |
   |---|---|
   | `POST /api/upload` | `POST /api/projects` → create `projects` row (`status=uploading`), then `POST /api/projects/{id}/source` (plain `UploadFile` for now) → save to `source_path`, set `source_bytes`, `status=queued`, kick analyze |
   | `GET /api/recent` | `GET /api/projects` → this user's projects, newest first, with `duration`, `clip count`, `rendered count`, `has_edits` |
   | `GET /api/jobs/{id}` | `GET /api/projects/{id}` → project + latest analyze job status + `analysis` (segments/clips) when ready |
   | `GET /api/jobs/{id}/events` | `GET /api/projects/{id}/analyze/events` (SSE, unchanged payload) |
   | `GET/POST /api/jobs/{id}/project` | `GET/POST /api/projects/{id}/edit` → `project_edits.state` |
   | `POST /api/jobs/{id}/render` | `POST /api/projects/{id}/render` |
   | `GET /api/jobs/{id}/render/events` | `GET /api/projects/{id}/render/events` |
   | `GET /api/jobs/{id}/clips` | `GET /api/projects/{id}/outputs` → from `render_outputs` |
   | `GET /media/{job_id}/source` etc. | `GET /media/{project_id}/source` / `/waveform.png` / `/frame.jpg` / `/clips/{name}` — **each loads the project, checks `project.user_id == current_user.id`, 404 otherwise**, then `FileResponse` from the per-user path |
   | `DELETE` (none) | `DELETE /api/projects/{id}` → remove media dir + cascade rows |
4. SFX: `GET /api/sfx` → builtin (from `sfx_assets` where `user_id IS NULL`) + this user's.
   `POST /api/sfx` → save under `user_sfx_dir`, insert row. `GET /media/sfx/{id}` → look up
   row, allow if builtin or owned.
5. `_run_analyze` / `_run_render` now take `job_id`, load project+job from DB, write progress
   to the `jobs` row (not the dict). `_get_job`, `JOBS`, "restored from disk" — deleted.
6. Analyze result → upsert `analyses` row + set `projects.duration_seconds`,
   `projects.status='ready'`. Render outputs → `render_outputs` rows.
7. Project create guard: `projects` count < `quota_projects`.

### Verification checklist
- User A creates project P; User B `GET /api/projects` → P absent; `GET /api/projects/{P}` →
  404; `GET /media/{P}/source` → 404.
- Files for P are under `media/users/<A>/projects/<P>/`; nothing under a bare `web_data/<id>/`.
- `DELETE /api/projects/{P}` → rows gone (analyses/edits/jobs/outputs cascade), dir removed.
- Full golden path for one user: create → upload → analyze SSE → `/edit` autosave round-trip
  → render SSE → outputs listed → clip downloads.
- Path traversal: `GET /media/{P}/clips/..%2f..%2fsource.mp4` → 400/404, never escapes.
- `GET /api/sfx` shows 5 builtins; upload one → appears with `user_id` set; User B doesn't see
  it.

### Anti-pattern guards
- No route without `current_verified_user` (except health + auth + landing).
- Never build a media path by string-concatenating a request value — go through `storage.py`,
  and re-check `.resolve().is_relative_to(media_root)`.
- Ownership check is on **every** media route, not just the JSON routes.
- `analyses.segments` can be ~1 MB JSONB — fine; don't also write it to disk.

---

# Phase 4 — Job queue: arq worker replaces BackgroundTasks + in-memory state

**Goal:** analyze and render run in a separate `worker` container off a Redis queue; job
state is 100% in Postgres; a worker crash can't lose a job.

> **✅ DONE (2026-09-04).** Files: `app/worker.py` (`WorkerSettings`: `analyze_task` /
> `render_task` thin wrappers over `app/jobs.py`; `on_startup` installs the Groq gate, synths
> builtin sfx, and sweeps `running` → `error "worker restarted"`; `max_jobs=2`, `max_tries=1`,
> `job_timeout=3600`), `app/queue.py` (`enqueue_analyze` / `enqueue_render` via
> `arq.create_pool`, set `jobs.arq_job_id`; `abort_job`), `app/groq_gate.py` (sync Redis
> per-minute token bucket sized to `groq_tokens_per_minute=8000`, fail-open, installed as
> `clipfinder.GROQ_GATE`), `app/sfx_synth.py` (builtin synth extracted so the worker doesn't
> import `app/sfx.py`'s auth deps). `clipfinder.py`: `GROQ_GATE` hook in `_groq_chat` +
> a 429/`retry-after` retry loop added to `transcribe` (Whisper). `app/projects.py`: dropped
> `BackgroundTasks`, calls `enqueue_*`, `_guard_enqueue` = **1 active job / user** (`429`) +
> **global analyze-queue depth** ≥ `max_global_queue` (`503`). `compose.yaml`: `x-app-env`
> anchor + `worker` service; `compose.override.yaml`: worker bind-mounts + `arq … --watch app`.
> Deviations: (a) Groq protection is a call-count-weighted **token bucket** (not a per-call
> semaphore) — concurrent analyses share the 8k-TPM window; Whisper stays ungated (separate
> limit) but got its own retry. (b) `run_in_threadpool` still appears in `app/projects.py` /
> `app/sfx.py` — those are **web-side** on-demand media helpers (waveform/frame ffmpeg), not
> job execution; `jobs.py` uses `asyncio.to_thread`. (c) the per-project "render already
> running" 409 became the broader per-user 429.
> Verified (Docker, real 22 s clip, Groq live and heavily rate-limited): upload → `jobs` row
> `queued` → arq picks up → `running` → `done`; `analyses` written; a 119 s Groq stall was
> ridden out by the gate+retry, job still completed. Concurrency: 2nd upload while a job runs
> → `429`. Render: `POST /render` 3 ranges → SSE `queued→running "Rendering clip 1 of 3"→
> done=1→2→3→done`, 3 `render_outputs`, 3 `render_clip` usage_events, all clips download 200,
> worker log `render … done — 3 clip(s)` in 1.8 s. Orphan sweep: upload → `restart worker`
> mid-analyze → job → `error "worker restarted"`; `max_tries=1` stopped arq re-running the
> cancelled task. `grep BackgroundTasks|JOBS\[ app/` → clean; worker does not import
> `app.main`. Image rebuilt.

### What to implement
1. `app/worker.py` — arq `WorkerSettings`: `redis_settings` from `settings.redis_url`,
   `functions=[analyze_task, render_task]`, `max_jobs=settings.worker_concurrency` (default
   **2**), `job_timeout=` (analyze 1800 s, render 3600 s), `on_startup` = synth builtin SFX +
   sweep orphans (`UPDATE jobs SET status='error', error='worker restart' WHERE status='running'`).
2. `analyze_task(ctx, job_id)` / `render_task(ctx, job_id)`:
   - open an async session, load `job` + `project`;
   - `status='running'`, `started_at=now()`;
   - build `cfg = get_cfg()`; call `pipeline.analyze(source_path, cfg, on_progress=…)` /
     `pipeline.render(..., on_clip=…, on_stage=…)` where the callbacks
     `await session.execute(update(Job)...)` + `commit()` (throttle to ≤ 2/s);
   - on success: persist `analyses` / `render_outputs`, `status='done'`, `finished_at`,
     write `usage_events` (`transcribe_seconds = duration`, one `render_clip` per output);
   - on exception: `status='error'`, `error=str(e)`.
   - `pipeline` is sync/blocking (ffmpeg, SDK) → run it via
     `await asyncio.get_running_loop().run_in_executor(None, functools.partial(...))` and have
     the callbacks push onto an `asyncio.Queue` the task drains, OR run the whole task
     synchronously in a thread and let callbacks do `asyncio.run_coroutine_threadsafe`. Pick
     the executor + drain pattern (documented in arq examples).
3. `app/queue.py` — `pool = await create_pool(redis_settings)`; `enqueue_analyze(job)` /
   `enqueue_render(job)` set `arq_job_id`. Called from the routes.
4. **Concurrency guard**: enqueue refuses if the user already has a `queued`/`running` job.
5. **Global backpressure**: `enqueue_analyze` checks `await pool.queued_jobs()` (or a Redis
   counter); over `settings.max_global_queue` → route returns `503`.
6. **Groq token-bucket**: `app/ratelimit.py` — Redis `INCRBY` on a per-minute key +
   `pexpire`; `clipfinder._groq_chat` / `transcribe` gains an optional
   `before_call: Callable` hook (passed via `cfg`) that blocks until budget. Keep
   `retry-after` handling.
7. SSE endpoints (`/analyze/events`, `/render/events`) already read the `jobs` row (Phase 3);
   confirm they work with multiple `web` workers (they poll the DB, so yes).
8. `compose.yaml` — add `worker` service (same image, `command: arq app.worker.WorkerSettings`,
   `depends_on` redis+postgres, shares the `media` volume, **no ports**). Scale note:
   `docker compose up -d --scale worker=2` — but total ffmpeg concurrency = `scale × max_jobs`;
   size to the VPS core count.

### Verification checklist
- Upload → `jobs` row `queued` → `worker` logs pick it up → `running` with rising
  `progress_pct` → `done`; `analyses` row written; SSE streamed the same frames as before.
- `docker compose restart worker` mid-analyze → that job → `error` "worker restart"; a fresh
  upload still processes.
- Enqueue a 2nd job for the same user while one runs → `409`.
- Two users upload at once with `max_jobs=1` → they run sequentially, both finish.
- Render path: `POST /render` with 3 ranges → 3 `render_outputs` + 3 `render_clip`
  usage_events; SSE `done` count reaches 3.
- Simulated Groq 429 (`retry-after`) → worker sleeps and retries, job still completes.
- `grep -rn "BackgroundTasks\|JOBS\[" app/` → **no matches**.

### Anti-pattern guards
- The worker must not import `app.main` (no FastAPI app in the worker) — share `models`,
  `db`, `pipeline`, `clipfinder`, `storage` only.
- Don't hold one DB transaction for the whole job — commit progress in small transactions.
- Don't `run_in_threadpool` (that's Starlette's, tied to the web process) — use arq's loop +
  `run_in_executor`.
- Progress writes throttled (≤ 2/s) — don't hammer Postgres per ffmpeg frame.
- `job_timeout` set on both tasks so a hung ffmpeg doesn't wedge a worker slot forever.

---

# Phase 5 — Quotas, limits, retention, abuse controls

**Goal:** the shared Groq bill and the VPS disk are bounded; a hostile signup can't ruin it.

> **✅ DONE (2026-09-04).** Files: `app/quota.py` (`monthly_minutes` / `storage_bytes` /
> `renders_today` / `project_count` over the session; `check_quota(session, user, action,
> extra_bytes=)` raises `QuotaError`; caps from Settings, per-user `quota_*_override` win),
> `app/account.py` (`GET /api/users/me/usage`), `app/admin.py` (superuser: `GET /users`,
> `POST /users/{id}/quota`, `DELETE /projects/{id}`, `POST /jobs/{id}/cancel`, `GET /queue`),
> `app/retention.py` (`run_gc` — warn window is *past warn, not past delete*; delete cascades
> rows + `rmtree` media; skips projects with a live job), `app/cli.py`
> (`python -m app.cli create-superuser | run-gc`). `app/worker.py`: `cron(retention_gc,
> hour=3, minute=0)`. `app/main.py`: `QuotaError` → 429 `{detail, limit, used, cap,
> resets_at}` handler + `GlobalRateLimitMiddleware` (per-IP/min, `/events` + health exempt,
> fail-open). `app/projects.py`: `check_quota` at project-create / upload (analyze-minutes +
> file-size + storage) / render-start; `upload_bytes` usage event written; streaming size
> guard unlinks a partial on 413. `app/email.py`: `send_retention_warning`. New Settings:
> `global_rate_limit_per_min`. Migration `2d1d3e3192e7` (`projects.retention_warned_at`).
> Deviations: (a) **slowapi dropped** from deps — the global limiter is the same ~15-line
> Redis helper as Phase 2's `rate_limit`. (b) `compose.yaml` web+worker gained
> `env_file: [.env]` so pydantic-settings picks up any `.env` var (quota knobs etc.) without
> restating each in `x-app-env`. (c) `on_after_register` skips `request_verify` when the user
> is already verified (the admin CLI path).
> Verified (Docker, test env with tiny caps): projects cap 2 → 3rd create 429
> `{limit:"projects",used:2,cap:2}` · minutes cap 0 → upload 429
> `{limit:"minutes",resets_at:"2026-10-01…"}` · admin `POST /users/{id}/quota {minutes:100}`
> → same upload then 200 · 1.2 MB file vs 400 KB cap → 413, project stays `uploading`, partial
> unlinked · `/api/users/me/usage` numbers match the DB · `/api/admin/users` lists 17 with
> per-user usage, non-admin → 403 · retention: 36 h-old project → `warned:1 deleted:0`,
> `retention_warned_at` set, "expires soon" email; 2nd run → `warned:0` (idempotent);
> 3 day-old project → deleted, media gone. `.env` restored, image rebuilt, prod caps back
> (60 min / 2 GB / 25 projects / 37 d).

### What to implement
1. `app/quota.py` — pure functions over the session:
   `monthly_minutes(user)`, `storage_bytes(user)`, `renders_today(user)`, `active_jobs(user)`,
   `project_count(user)`. One `async def check_quota(user, action, *, extra_bytes=0)` that
   raises `QuotaError(limit, used, resets_at)` → mapped to `429` + JSON.
2. Call `check_quota` at: project create, source upload (size + storage), analyze enqueue
   (minutes + verified + concurrency), render enqueue (daily + concurrency).
3. `usage_events` writes already added in Phase 4 — confirm `transcribe_seconds` uses the
   real probed duration, and `upload_bytes` is written on finalize.
4. `GET /api/users/me/usage` → `{minutes:{used,cap}, storage:{used_mb,cap_mb}, renders_today:{used,cap}, projects:{used,cap}, plan:"free"}`.
5. **Retention** — `cron_gc(ctx)` in `app.worker` (arq `cron(hour=3, minute=0)`):
   - projects with `coalesce(last_opened_at, created_at) < now() - retention_warn_days` and
     not yet warned → send "expires in N days" email, set `retention_warned_at`;
   - `< now() - retention_delete_days` → delete media dir + cascade rows, log it.
   - `retention_delete_days=0` disables.
6. **Caddy hardening** — `request_body max_size 2GB` (matches quota), `header` block
   (HSTS, `X-Content-Type-Options nosniff`, `Referrer-Policy strict-origin-when-cross-origin`),
   `encode zstd gzip`.
7. **slowapi global** — a modest default limit on all `/api/*` (e.g. 120/min/IP) on top of the
   auth-specific limits.
8. Superuser endpoints `/api/admin/*` (guard `current_user(superuser=True)`): list users +
   usage, set `quota_*_override`, force-delete a project, cancel a job (`await pool.abort_job(arq_job_id)`).

### Verification checklist
- Analyze a video that would cross 60 min/month → `429` `{limit:"minutes",used,resets_at}`;
  SPA shows it.
- Upload > 2 GB → rejected (size check) with a clear message.
- Fill a user to 5 GB → next upload → `429 storage`.
- 41st render in a day → `429 renders`.
- `GET /api/users/me/usage` numbers match `usage_events` sums.
- Set `retention_delete_days=1`, run `cron_gc` manually
  (`docker compose exec worker python -c "..."`), an old project's dir + rows vanish, a warning
  email fired first.
- Admin bumps User X's `quota_minutes_override` to 600 → X can now exceed 60.

### Anti-pattern guards
- Quota checks are **server-side only** — the SPA showing usage is cosmetic.
- `check_quota` runs **before** enqueue and again is cheap enough to run in the tusd
  pre-create hook (Phase 6).
- Retention delete is idempotent and logs what it removed; never deletes a project with a
  `running` job.
- Don't compute storage by walking the filesystem on every request — sum `source_bytes` +
  `render_outputs.bytes` from the DB.

---

# Phase 6 — Resumable uploads (tusd)

**Goal:** multi-GB uploads survive flaky connections; upload authz + quota happen before a
byte lands.

> **✅ DONE (2026-09-04).** Files: `app/uploads.py` (`POST /api/upload/hooks` — dispatch on
> `Type`; `_user_from_cookie` decodes the forwarded `cfa` JWT via
> `JWTStrategy.read_token`; `pre-create` → 401 if not verified, 404 if project not owned,
> 409 if already processing, 429 on concurrency / `check_quota("analyze"|"upload")`, else
> `{}`; `post-finish` → `os.replace` the staged file into `storage.source_path`, delete the
> `.info`, set `projects` → `queued`, add `Job` + `upload_bytes` `UsageEvent`,
> `enqueue_analyze`). `app/csrf.py` + `app/ratelimit.py`: `/api/upload/hooks` exempted.
> `app/main.py` lifespan: create `media/_uploads` `chmod 0o777` (tusd runs as uid 1000).
> `app/retention.py`: `_sweep_stale_uploads` (24 h) folded into `run_gc` — replaces the
> nonexistent `-upload-expiration` flag. `compose.yaml`: `tusd` service
> (`ghcr.io/tus/tusd:v2`, `-base-path=/files/ -behind-proxy -upload-dir=/media/_uploads
> -max-size=2GiB -hooks-http=…/api/upload/hooks -hooks-http-forward-headers=Cookie,Authorization
> -hooks-enabled-events=pre-create,post-finish,post-terminate`). `Caddyfile`: `/files /files/*`
> → `reverse_proxy tusd:1080 { flush_interval -1 }` (Caddyfile edits need
> `docker compose restart caddy`).
> Deviations: (a) tusd rejections use `{"RejectUpload":true,"HTTPResponse":{"StatusCode":…}}`
> (clean client status) not non-2xx (which tusd turns into 500). (b) `-upload-expiration` isn't
> a flag in this tusd build → the GC sweep covers it. (c) the plain `POST
> /api/projects/{id}/source` (Phase 3) stays as the small-file / no-JS fallback.
> Verified (raw tus 1.0.0 client via node, through Caddy): `OPTIONS /files/` → tusd
> (`Tus-Max-Size: 2147483648`); `POST /files/` **no cookie → 401 "Sign in to upload."**;
> with cookie → 201 + `Location`; **PATCH half → 204 offset=633313 → HEAD → offset 633313
> (resume point) → PATCH rest from offset → 204 offset=full → UPLOAD COMPLETE**; `post-finish`
> hook → project `uploading→analyzing`, analyze job `running`, `source.mp4` now under
> `media/users/<uid>/projects/<pid>/`, `media/_uploads/` empty; bob tus-uploads into alice's
> projectId → **404 "Project not found."** Image rebuilt.

### What to implement
1. `compose.yaml` — `tusd` service: `tusio/tusd`, `command: -upload-dir /media/_uploads
   -hooks-http http://web:8000/api/upload/hooks -hooks-http-forward-headers Authorization,Cookie
   -behind-proxy -max-size 2147483648`. Mounts the `media` volume.
2. `Caddyfile` — `reverse_proxy /files/* tusd:1080`.
3. `POST /api/upload/hooks` on `web` — dispatch on `Hook-Name`:
   - `pre-create`: authenticate from the forwarded `Cookie` (reuse the fastapi-users strategy
     manually), read `Upload-Length` + metadata (`projectId`, `filename`), run
     `check_quota(user, "upload", extra_bytes=length)`; reject → non-2xx (tusd aborts).
   - `post-finish`: look up the project (owned, `status=uploading`), `rename` the staged file
     into `source_path`, set `source_bytes` + `status=queued`, write `upload_bytes`
     usage_event, `enqueue_analyze`.
4. Frontend (Phase 7) uses **Uppy** + Tus plugin → endpoint `/files/`, metadata carries
   `projectId`. Fallback plain `POST /api/projects/{id}/source` stays for small files / tests.
5. tusd upload expiry: `-hooks-http` `pre-finish` optional; set tusd
   `-upload-expiration` so abandoned partials are swept.

### Verification checklist
- Upload a 1.5 GB file via Uppy; `docker compose pause` the network mid-upload, unpause →
  resumes from the last chunk, completes, project → `queued` → analyzed.
- `pre-create` for a user already at storage cap → tusd returns an error to the client, no
  file staged.
- Small file (5 MB) via the plain fallback route still works.
- Abandoned upload's partial in `_uploads/` is gone after `-upload-expiration`.
- A user cannot finish an upload into **another** user's `projectId` (hook ownership check).

### Anti-pattern guards
- The finish hook `rename`s (same volume = atomic); never `copy` a multi-GB file.
- Don't trust tusd metadata blindly — re-check project ownership + status in the hook.
- Caddy must not buffer `/files/*` (`flush_interval -1` / streaming) — tusd needs the raw
  stream.
- `-max-size` on tusd == the Caddy body cap == the quota max upload. Keep the three in sync
  (one env var).

---

# Phase 7 — Frontend: React + Vite + TypeScript SPA

**Goal:** replace `web/` with a built SPA. Server API contracts from Phases 2–6 stay fixed;
this phase only consumes them. Keep the visual design.

> **7a + 7b + 7c ✅ DONE (2026-09-04). 7d = polish, mostly folded in.**
> `frontend/` — hand-scaffolded Vite 6 + React 19 + TS (`npm create vite` hung interactively).
> Deps: react-router-dom 7, @tanstack/react-query 5, zustand 5, @uppy/core+tus 4,
> @fontsource/{space-grotesk,instrument-sans} (self-hosted — no `fonts.gstatic.com`).
> `src/styles/{tokens,global}.css` port `web/styles.css` (single dark world, no light theme).
> `src/vendor/background.js` = copied WebGL bg + `<Background>` wrapper with a text scrim.
> `src/api/client.ts` (cookie `credentials:"include"`, `X-CSRF-Token` from the `csrftoken`
> cookie on unsafe methods, 401 → `auth:expired` event; no refresh endpoint → 1 h sessions),
> `src/api/types.ts`. `AuthProvider` + `RequireAuth`/`RequireVerified` guards. Pages: Landing
> (honest hosted-app copy — "the video uploads", not "stays on disk"), Login (`?verified=1`
> banner), Register, Verify (token link → `/login?verified=1` when not signed in, `/app` when
> signed in), Forgot, Reset, Projects (list + `<input type=file>` → create project → Uppy Tus
> upload to `/files/` with `{projectId, filename}` metadata → navigate to editor), Account
> (usage meters from `/api/users/me/usage`), Editor (**7b subset** — analyze progress via
> `useSSE`, the clip shortlist with keep toggles, render-all + render SSE + rendered-list;
> **no timeline / player / caption / crop / sfx yet**), NotFound. `AppShell` nav + chrome
> Wordmark + Toast.
> Serving: `Dockerfile` gained a `node:22` frontend stage → `npm run build` →
> `COPY --from=frontend /fe/dist ./web`; `app/main.py` replaced the StaticFiles mount with an
> `/assets` mount + a `GET /{full_path:path}` SPA fallback (serves `index.html` for client
> routes, never shadows `api/`/`media/`/`files/`). `compose.override.yaml` dropped the
> `./web` bind-mount. Dev: `cd frontend && npm run dev` (Vite :5173, proxies `/api` `/media`
> `/files` → `localhost:8000`). Deviations: (a) `npm install` needs `--ignore-scripts`
> locally (a postinstall `spawnSync` fails under the Windows/RTK setup); the Linux Docker
> build runs `npm ci` normally. (b) `@types/node` added for `vite.config.ts` typecheck.
> (c) old `web/` kept as reference for the 7c port; delete when 7c lands.
> Verified (headless browser through Caddy :8000): landing renders (chrome wordmark, hero,
> glass step cards, WebGL bg); register → "Check your email"; Mailpit link → `/verify` →
> `/login?verified=1`; login → `/app` empty state; `/account` shows
> "60 min / 5120 MB / ..." meters; **0 JS errors, 0 horizontal overflow**; text contrast
> fixed with a background scrim; `frontend/dist` has no `fonts.gstatic`/`googleapis` refs.
> All 7 compose services healthy; `/`, `/app`, `/api/health` → 200 through Caddy.
>
> **7c — editor (2026-09-04).** `src/editor/`: `types.ts` (`ClipEdit` per-clip: keep, trimmed
> start/end, caption{on,style,position,size,anim,karaoke}, look{vertical,mode,px,py,zoom,blur},
> color{preset,b,c,s}, sfx[], sfxAuto, autoOff; `defaultEdit` + `applyAuto` seeds from the
> analysis's per-clip `auto`). `store.ts` — Zustand: clips/segments/duration/snaps/edits/
> activeIdx/playhead + debounced 900 ms autosave `POST /api/projects/:id/edit` (`{v:2,
> activeIdx, edits}` JSONB — my own shape, only the SPA reads it back; `keptRanges()` builds
> the `pipeline.render` ranges: `{start,end,vertical,label,crop|null,captions|null,color|null,
> sfx:[{file,at,gain}]}` incl. `sfxAuto` → `whoosh.m4a@0`). Components: `Player` (`<video
> src=/media/:id/source>`, live CSS crop preview via `object-fit/position/scale` + a colour
> `filter()` matching the presets, transport, clamps playback to the clip's trimmed bounds),
> `Timeline` (waveform PNG bg, other-clip blocks, active band + two pointer-drag trim handles
> that snap to segment boundaries, scrub, playhead), `ClipList` (chips + keep/skip + ✨ AI-edit
> toggle showing `active.why`), `Inspector` (tabs Caption / 9:16 / SFX — `Seg`/`Slider`/
> `Toggle` control kit), `RenderPanel` (build ranges → `POST /render` → render SSE →
> `<video>` gallery with download). `Editor.tsx` composes it + keyboard (space, ←/→ ±frame/
> ±5s, i/o set in/out). Old `web/` deleted (`background.js` already copied into `src/vendor/`).
> Verified (headless browser, project seeded with 3 synthetic clips incl. an `auto` block):
> login → `/app/p/<id>` → 3 clip chips, player (real frame decoded), waveform timeline,
> Caption/9:16/SFX tabs; **the AI auto-edit applied to clip 1** (vertical + Bold caption + Pop
> anim + Karaoke all pre-set); toggling 9:16 fired autosave; `/api/projects/:id/edit` → `v2
> state saved, edits: 3`; **Render 3 clips → worker cut them in 5.3 s**, `01_the-setup_cc.mp4`
> came out with `captions=t, sfx_count=2` (the auto-edit's whoosh+impact mixed in). 0 JS
> errors, 0 horizontal overflow. **Not ported** (7d backlog): per-caption-line text editing,
> drag-to-place captions on the preview, free-form/no-clips mode, `?` shortcut modal.

### 7a — Scaffold + shell + auth pages
- `frontend/` — `npm create vite@latest frontend -- --template react-ts`. Add
  **react-router-dom**, a store (**Zustand** — small, fits the editor's imperative state),
  **@tanstack/react-query** for server state, **Uppy** (`@uppy/core @uppy/tus @uppy/react`).
- Port `web/styles.css` design tokens → `src/styles/tokens.css` (global). Self-host
  **Space Grotesk** + **Instrument Sans** (woff2 in `src/assets/fonts/`, `@font-face`), delete
  the Google Fonts `@import`.
- Port `web/vendor/background.js` → `<LiquidGlassBackground />` (canvas, `useEffect` mount).
- `src/api/client.ts` — `fetch` wrapper: `credentials: "include"`, injects `X-CSRF-Token` from
  the `csrftoken` cookie on unsafe methods, on `401` hits `/api/auth/refresh` once then
  retries, else redirects to `/login`.
- `src/auth/` — `AuthProvider` (calls `/api/users/me` on mount), `<RequireAuth>` route guard,
  `<RequireVerified>`.
- Pages: `/` Landing (port the marketing sections + FAQ from `index.html`), `/login`,
  `/register`, `/verify` (reads `?token`, calls verify), `/forgot`, `/reset` (reads `?token`),
  `/app` (project list), `/app/p/:id` (editor), `/account`.
- Caddy serves `frontend/dist` at `/`, SPA fallback (`try_files {path} /index.html`).
  `Dockerfile.web` = multi-stage: `node:22` build → copy `dist` into the caddy image (or a
  shared volume).

### 7b — Project list + upload + analyze
- `/app`: `useQuery(['projects'])` → cards (name, duration, clip count, edited badge, delete).
  Empty state + skeletons (port `.empty` / `.skel` CSS).
- "New project": create → Uppy Tus upload to `/files/` with `projectId` metadata → on complete,
  navigate to `/app/p/:id`.
- Editor mounts → `useQuery(['project', id])`; if analyzing, open
  `new EventSource('/api/projects/'+id+'/analyze/events')` → progress bar (port the stage
  copy). On `done`, refetch → Review step.

### 7c — Editor (the bulk — port `web/app.js` ~1555 lines)
Components, each owning the slice of `S` it needs (Zustand store mirrors today's `S`):
- `<Timeline>` — waveform bg (`/media/:id/waveform.png`), markers from `clips`, drag handles,
  snapping (`S.snaps` from segment bounds), `pct()`/`xToTime()`.
- `<Player>` + `<Transport>` — `<video src="/media/:id/source">`, play/pause/mute/step,
  frame-step, `k` preview selection. Port the `#vtransport` logic.
- `<ReviewList>` — `S.kept` toggles, per-clip cards.
- `<Inspector>` tabs — `<CaptionPane>` (style, position, size, anim, per-line text + drag
  position, karaoke), `<SfxPane>` (library, per-add placement, auto toggle), `<LookPane>`
  (9:16 modes crop/fill/subject, zoom, blur, colour preset + b/c/s).
- `<AutoBadge>` — `maybeApplyAuto` seeding + toggle (`autoApplied`/`autoOff`).
- `<RenderQueue>` + `<Gallery>` — build `ranges`, `POST /render`, `EventSource` render
  progress, incremental gallery from `render_outputs`.
- Autosave: debounce the store → `POST /api/projects/:id/edit` (port `projectState()` /
  `hasEdits()` shape **exactly** so the JSONB stays compatible).
- Keyboard shortcuts (`?` modal, space/m/i/o/k/,/./[/]/Ctrl+Enter) — one `useHotkeys` hook.

### 7d — Account + polish
- `/account` — `useQuery(['usage'])` → minute/storage/render/project meters; change password
  (`/api/auth/…`); delete account (superuser-guarded confirm → cascade).
- Reduced-motion, focus rings, mobile (port the existing media queries), error toasts.

### Verification checklist (real browser, not headless — headless Edge can't decode the video)
- Register → verify → login → land on `/app`, all via the SPA.
- New project → resumable upload → analyze progress → Review shows the AI clips.
- Editor golden path: pick a clip, drag both handles, edit a caption line, drag its position,
  try all 3 vertical modes (blur bars never black), add an SFX, set a colour preset, add to
  queue, render, play the result **with sound**.
- Refresh mid-edit → autosave restored (`project_edits` round-trip).
- `/account` meters match `/api/users/me/usage`.
- Lighthouse: no horizontal scroll at 390 / 1440; `fonts.gstatic.com` not requested.
- `grep -rn "fonts.googleapis" frontend/` → none.

### Anti-pattern guards
- Server ffmpeg/ASS/Groq logic is **not** reimplemented in JS — the SPA only draws UI and
  calls the API.
- Don't change the `project_edits.state` / `analyses.clips` JSON shapes — they're the
  contract with the worker and `pipeline.build_ass`.
- Token never touches JS-readable storage; rely on the cookie.
- One store, not prop-drilled `S` — but keep the shape recognizably the old `S` for porting.
- EventSource: close on unmount, reconnect with backoff, stop on `done`/`error`.

---

# Phase 8 — Deploy + hardening

> **✅ DONE (2026-09-04).** Files: `DEPLOY.md` (provision → configure → first boot → upgrade
> / rollback → backups + restore → monitoring → capacity → admin), `.env.example` (full, with
> `openssl rand` instructions + all quota/ops knobs), `.github/workflows/ci.yml` (ruff +
> `alembic upgrade head` + autogen-drift check + import smoke on a service PG/Redis; frontend
> `npm ci && npm run build`). `compose.yaml`: `x-logging` anchor (json-file 20 MB × 5) on
> every service; **dedicated `backup` service** (`postgres:18-alpine`, 24 h loop
> `pg_dump | gzip` → `backups` volume, keeps `BACKUP_KEEP`) — client version always matches
> the server; `backups` volume. `app/main.py` `/api/health` now returns
> `{ok, db, redis, disk_free_pct, disk_ok, queue_depth}` and **503 when the media disk is
> below `DISK_MIN_FREE_PCT` (default 4 %)**. `app/quota.py` `assert_disk_ok()` → `QuotaError
> ("server_storage")` gates uploads (via `check_quota("upload")`). `config.py`
> `disk_min_free_pct`. `compose.override.yaml` Mailpit pinned `v1.21`.
> Deviations: (a) backup is a compose service, not an arq cron — `pg_dump` 17 (the only client
> in Debian trixie) refuses a pg 18 server (`server version mismatch`); the `postgres:18`
> image sidesteps it. `app/backup.py` + the CLI `backup` subcommand were removed. (b) old
> `web/` dir deleted (Phase 7c). (c) `run_in_threadpool` in `app/projects.py` / `app/sfx.py`
> is web-side media generation (waveform / frame / sfx synth), not job execution — allowed.
> Verified (local): `/api/health` → `{ok, db, redis, disk_free_pct:93, disk_ok, queue_depth:0}`;
> `DISK_MIN_FREE_PCT=99` → health **503** `disk_ok:false`, revert → 200; the `backup` service
> wrote `clipfinder-<ts>.sql.gz` containing every `CREATE TABLE`; `docker compose down &&
> up -d` → users/projects/render_outputs `26/12/7` unchanged, 6 `source.mp4` intact, Caddy
> served without a cert re-request; superuser CLI + `run-gc` still work; `compose.yaml config`
> valid; all 8 services healthy. Anti-pattern grep of `app/` + `frontend/src/`: clean; no
> `:latest` in `compose.yaml`; no `fonts.googleapis` / `localStorage` token in the SPA.
> Not exercisable here (documented in DEPLOY.md, standard mechanisms): a real VPS + public
> HTTPS cert, real transactional email, a full Postgres restore.

### What to implement
- **DNS + TLS**: point `A`/`AAAA` at the VPS; `Caddyfile` `{$DOMAIN}` + `{$ACME_EMAIL}`;
  first `docker compose up -d` provisions the cert (persisted in `caddy_data`).
- **Secrets**: `.env` on the box (0600) or Docker secrets for `jwt_secret`, the 3 token
  secrets, `groq_api_key`, `postgres` password, SMTP creds. `openssl rand -hex 32` each.
- **Backups**: arq cron `pg_dump` nightly → gzip → `restic`/`rclone` to offsite (B2/S3).
  Media: either accept loss for the free tier, or `restic` the `media` volume weekly.
  Document the restore.
- **Monitoring**: `/api/health` hit by an uptime pinger; a `disk_free` gauge in health that
  alerts when the `media` volume < 10% (disk-full = outage); worker queue-depth in health.
  Optional **Sentry** (`sentry-sdk[fastapi]`) for `web` + `worker`.
- **Logs**: `docker compose logs` + `json-file` driver with rotation
  (`max-size: 20m, max-file: 5`). Optional Loki later.
- **Postgres**: `shared_buffers` / `work_mem` tuned for the VPS; `pgbouncer` only if
  connection count bites (unlikely at this scale).
- **CI** (optional): GitHub Actions — `ruff`, `pytest` (unit-test quota/storage/auth),
  `alembic upgrade head` on a throwaway PG, `npm run build`.
- **Runbook** (`DEPLOY.md`): provision, first boot, run migrations
  (`docker compose run --rm web alembic upgrade head`), create the first superuser
  (`docker compose run --rm web python -m app.cli create-superuser`), rollback (down a
  migration, redeploy previous image tag), restore from backup.

### Verification checklist
- Fresh VPS: clone, `cp .env.example .env` + fill, `docker compose up -d`,
  `docker compose run --rm web alembic upgrade head` → site live on HTTPS with a valid cert.
- Register → verify (real email) → upload → analyze → edit → render → download, end to end,
  over the public domain.
- `docker compose down && docker compose up -d` → no cert re-request (persisted), data intact.
- Kill the VPS's Postgres container, restore from last `pg_dump` → projects/users back.
- Fill the media disk in staging → health goes red, uploads 503, existing playback still
  works.
- `docker compose exec web python -m app.cli create-superuser` works; superuser sees
  `/api/admin/users`.

### Anti-pattern guards
- Never `:latest` for `caddy`, `postgres`, `redis`, `tusd` — pin minor versions.
- `caddy_data` and `pgdata` are **named volumes**, never bind mounts to `/tmp`.
- Migrations run as an explicit step, never auto-on-boot (a bad migration shouldn't crash-loop
  the web service).
- Don't expose Postgres/Redis/tusd ports to the host in prod (`compose.override.yaml` only).
- `jwt_secret` rotation = all users logged out; document it, don't do it casually.

---

# Final Phase — Verification

1. **Contract check** — every SSE payload and JSON shape the SPA consumes matches Phases 2–6.
   `grep -rn "EventSource\|/api/projects\|/media/" frontend/src` → cross-check each against the
   FastAPI route.
2. **Anti-pattern grep** (repo-wide):
   - `grep -rn "declarative_base\|python-jose\|passlib\|BackgroundTasks\|run_in_threadpool\|JOBS\[\|web_data/\|/api/browse\|_settings.json\|localStorage.*token" app/ frontend/src/` → **empty**.
   - `grep -rn ":latest" compose.yaml Caddyfile Dockerfile*` → empty.
   - `grep -rn "fonts.googleapis" frontend/` → empty.
3. **Isolation test** — script: create 2 users, each 1 project; assert every cross-user
   `GET`/`DELETE` on projects, edits, media, outputs, sfx → 403/404.
4. **Quota test** — script drives each limit to its edge → `429` with the right `limit` key.
5. **Crash test** — `docker compose kill worker` mid-analyze and mid-render → jobs → `error`,
   no zombie rows, next job fine.
6. **Load smoke** — 5 concurrent users upload a 5-min clip; the global Groq bucket serializes
   the transcription calls, all 5 analyses finish, no Groq 429 escapes to the user.
7. **Fresh-deploy test** — Phase 8's "fresh VPS" checklist, clean.
8. **Golden path in a real browser** — register → … → download a rendered clip with audio.

---

## Sources (Phase 0)

- FastAPI auth 2026 (Argon2/pwdlib, PyJWT over python-jose, custom vs framework):
  workos.com/blog/top-authentication-solutions-fastapi-2026 ·
  tomodahinata.com/en/blog/fastapi-authentication-oauth2-jwt-security-scopes-production-guide
- fastapi-users 15.0.5 status + features: pypi.org/project/fastapi-users ·
  github.com/fastapi-users/fastapi-users · fastapi-users.github.io/fastapi-users/latest/
- arq vs Celery/Dramatiq/RQ for async FastAPI:
  blog.rajpoot.dev/posts/fastapi/fastapi-background-tasks-2026/ ·
  davidmuraya.com/blog/fastapi-background-tasks-arq-vs-built-in/ ·
  markaicode.com/vs/celery-alternatives/
- SQLAlchemy 2.0 async + Alembic + asyncpg, current versions (SA 2.0.44 / asyncpg 0.31 /
  Alembic 1.17.1 / PG 18): medium.com/@rosewabere/building-a-production-grade-async-backend-with-fastapi-sqlalchemy-postgresql-and-alembic ·
  berkkaraal.com/blog/2024/09/19/setup-fastapi-project-with-async-sqlalchemy-2-alembic-postgresql-and-docker/
- Resumable uploads / tus / tusd + hooks: tus.io · github.com/tus/tusd ·
  hub.docker.com/r/tusio/tusd · pypi.org/project/fastapi-tusd · buildo.com/blog-posts/resumable-large-file-uploads-with-tus
- Caddy v2 + Docker Compose auto-HTTPS (pin versions, persist /data):
  oneuptime.com/blog/post/2026-01-16-docker-caddy-automatic-https/view ·
  nerdleveltech.com/caddy-reverse-proxy-docker-compose-production-https-tutorial
- Current repo: `server.py`, `pipeline.py`, `clipfinder.py`, `web/app.js`, `clipfinder.toml`.
