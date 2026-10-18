# Deploying clipfinder

Single VPS, Docker Compose. Services: `caddy` (TLS + reverse proxy + built SPA), `web`
(FastAPI), `worker` (arq), `tusd` (resumable upload), `postgres`, `redis`.

## 1. Provision the box

- 2+ vCPU, 4+ GB RAM, 40+ GB disk (media grows fast — see retention below).
- Install Docker Engine + the compose plugin.
- Point an `A` (and `AAAA`) DNS record for your domain at the box.
- Open ports 80 and 443.

## 2. Configure

```bash
git clone <repo> clipfinder && cd clipfinder
cp .env.example .env
chmod 600 .env
```

Fill `.env`:

```bash
# generate the four secrets
openssl rand -hex 24   # -> POSTGRES_PASSWORD
openssl rand -hex 32   # -> JWT_SECRET
openssl rand -hex 32   # -> VERIFICATION_TOKEN_SECRET
openssl rand -hex 32   # -> RESET_PASSWORD_TOKEN_SECRET
```

Set `DOMAIN`, `ACME_EMAIL`, `PUBLIC_BASE_URL=https://<domain>`, `COOKIE_SECURE=true`,
`ENVIRONMENT=prod`, `GROQ_API_KEY`, and the `SMTP_*` block for your email provider.

## 3. First boot

```bash
docker compose -f compose.yaml build
docker compose -f compose.yaml up -d postgres redis
docker compose -f compose.yaml run --rm web alembic upgrade head
docker compose -f compose.yaml up -d
```

`-f compose.yaml` alone excludes `compose.override.yaml` (which is dev-only: hot reload,
Mailpit, exposed DB/Redis ports).

Caddy provisions the TLS cert on first request to the domain. Watch `docker compose logs -f caddy`.

Create the first admin:

```bash
docker compose -f compose.yaml exec web python -m app.cli create-superuser \
  --email you@example.com --password '<pick-a-strong-one>'
```

Verify: open `https://<domain>`, register a throwaway account, click the verification
email, sign in, upload a short clip, watch it analyze, render one, download it.

## 4. Upgrades

```bash
git pull
docker compose -f compose.yaml build
docker compose -f compose.yaml run --rm web alembic upgrade head   # explicit — never auto
docker compose -f compose.yaml up -d
```

`docker compose down && up -d` keeps all data (`pgdata`, `media`, `caddy_data` are named
volumes) and does **not** re-request the cert.

### Rollback

```bash
docker compose -f compose.yaml run --rm web alembic downgrade -1   # if the release added a migration
git checkout <previous-tag>
docker compose -f compose.yaml build && docker compose -f compose.yaml up -d
```

## 5. Backups

The `backup` service (a `postgres:18-alpine` container) runs `pg_dump | gzip` every 24 h
into the `backups` volume, keeping the last `BACKUP_KEEP` (default 7). Run one now:

```bash
docker compose -f compose.yaml exec backup sh -c \
  'pg_dump --no-owner --no-privileges | gzip > /backups/manual-$(date -u +%Y%m%d-%H%M%S).sql.gz'
docker compose -f compose.yaml exec backup ls -lh /backups
```

**Ship it offsite** — the volume lives on the same disk. Cron on the host, e.g.:

```bash
0 5 * * *  docker run --rm -v clipfinder_backups:/b -v /root/.config/rclone:/c \
  rclone/rclone sync /b remote:clipfinder-backups
```

Media (`clipfinder_media`) is large; either `restic`/`rclone` it weekly the same way, or
accept that a disk loss means users re-upload (the DB rows survive, sources don't).

### Restore Postgres

```bash
docker compose -f compose.yaml stop web worker
gunzip -c /path/to/clipfinder-YYYYMMDD-HHMMSS.sql.gz \
  | docker compose -f compose.yaml exec -T postgres psql -U clipfinder -d clipfinder
docker compose -f compose.yaml up -d
```

For a clean restore, `docker compose down -v` first, `up -d postgres`, then pipe the dump
into a fresh DB before `alembic upgrade head` (usually a no-op) and `up -d`.

## 6. Monitoring

- Point an uptime pinger at `https://<domain>/api/health`. It returns
  `{ok, db, redis, disk_free_pct, disk_ok, queue_depth}` and 503 when `db`/`redis` are down
  **or the media disk is below `DISK_MIN_FREE_PCT`** — a full disk is the most likely outage.
- `queue_depth` climbing and not draining = the worker is stuck or Groq is rate-limiting;
  `docker compose logs -f worker`.
- Container logs rotate (json-file, 20 MB × 5). `docker compose logs -f`.
- Optional: set `SENTRY_DSN` (add `sentry-sdk[fastapi]` and init in `app/main.py` + `app/worker.py`).

## 7. Capacity notes

- **Pick a dedicated-vCPU box, not burstable/shared.** ffmpeg is CPU-bound; shared vCPUs
  (e.g. plain DigitalOcean Droplets, AWS `t`-series) throttle hard once a render runs a
  while, and that throttling is *inconsistent* — the same clip renders at different speeds
  depending on what else the host is doing, which users will notice. Hetzner's `CCX` line
  (dedicated vCPU) is the recommended default: **CCX23 (4 vCPU / 16 GB)** comfortably
  covers ~1,000 users with light-to-moderate concurrent activity; go **CCX33 (8 vCPU)** if
  renders start queueing at peak hours.
- **Set `WORKER_CONCURRENCY` and `FFMPEG_THREADS` together — this is what actually makes
  the app feel the same for every user.** Left unset, every concurrent ffmpeg process
  auto-detects and grabs *every* core; two renders at once already means two processes
  fighting over the same cores, and render time becomes a function of how many other users
  happen to be active right now — not something a user can predict. Instead size them so
  `WORKER_CONCURRENCY × FFMPEG_THREADS ≈ vCPU count − 1` (leave one core for
  web/postgres/redis/tusd, which are light):

  | Box | `WORKER_CONCURRENCY` | `FFMPEG_THREADS` |
  |---|---|---|
  | CCX23 (4 vCPU) | 3 | 1 |
  | CCX33 (8 vCPU) | 3 | 2 |

  This gives every job a fixed, equal slice regardless of load — job N+1 waits in the arq
  queue (roughly FIFO) rather than slowing down jobs already running. Uncomment the
  `deploy.resources.limits.cpus` guard on `worker` in `compose.yaml` as a hard backstop
  (set it to vCPU count − 1) so a misconfigured value can't starve the rest of the stack.
- `WORKER_CONCURRENCY` × worker **replicas** = max concurrent ffmpeg jobs.
  `docker compose -f compose.yaml up -d --scale worker=2` for more throughput on a bigger
  box — but the Groq token-bucket (`GROQ_TOKENS_PER_MINUTE`, default 8000 = the free tier)
  still serializes their transcription calls across *all* workers; that's the real ceiling
  on simultaneous *analyses* (not renders) at open-signup scale — see the free-tier vs.
  paid-tier tradeoff below.
- **The shared Groq key, not the VPS, is usually the first bottleneck for concurrent
  *analysis*.** At 8,000 tokens/minute (free tier) roughly one pick call fits per minute;
  raise `GROQ_TOKENS_PER_MINUTE` to match a paid Groq tier once real usage approaches that.
- Retention GC (`RETENTION_DELETE_DAYS`, default 37) deletes idle projects + their media.
  Lower it if the disk fills; set to 0 only if you have plenty of space.
- `docker compose -f compose.yaml exec worker python -m app.cli run-gc` runs it on demand.

## 8. Admin

Superusers get `/api/admin/*`: `GET /users` (list + per-user usage), `POST
/users/{id}/quota` (`{minutes, storage_mb}` overrides), `DELETE /projects/{id}`, `POST
/jobs/{id}/cancel`, `GET /queue`.
