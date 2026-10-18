# clipfinder

Find short-form clips in long videos — transcribe with Groq Whisper, pick the best
moments with an LLM (Groq free tier by default, Claude optionally), then reframe,
caption, color-grade and render them. Self-hosted, multi-user, open signup.

## Architecture

FastAPI (`app/`) + async SQLAlchemy + Postgres + Redis + an `arq` worker + a React/Vite
frontend (`frontend/`), served behind Caddy with resumable uploads via `tusd`. Everything
runs as one Docker Compose stack (`compose.yaml`). The core engine (`clipfinder.py`,
wrapped by `pipeline.py`) is unchanged from clipfinder's original single-user design —
the app around it handles accounts, quotas, job queuing and multi-tenant storage.

- `clipfinder.py` — transcribe, pick clips (Groq or Claude), verify/refine, cut, caption
- `pipeline.py` — thin `analyze()`/`render()` wrapper the web app calls
- `app/` — FastAPI app: auth (cookie-based, fastapi-users), projects/jobs/editor routes,
  quotas, the credit ledger, AI thumbnails, the arq worker
- `frontend/` — the SPA: project list, upload, the clip editor, render panel
- `alembic/` — DB migrations

## Running it

```powershell
cp .env.example .env    # fill in GROQ_API_KEY at minimum
docker compose up -d
docker compose exec web alembic upgrade head
cd frontend && npm run dev   # dev only — prod serves the built SPA via Caddy/web
```

Open `http://localhost:8000` (or the Vite dev server's URL for hot reload). Full
production deployment steps (VPS provisioning, secrets, TLS, backups, capacity sizing)
are in `DEPLOY.md`.

## Project history

- `PLAN-multiuser.md` — the original 8-phase migration from a local single-user CLI/tool
  to this multi-user app.
- `PLAN-boost.md` — the feature roadmap built on top of it (taste-driven picking,
  auto-render, content-category detection, trend-aware scoring, the credit ledger,
  multi-provider AI thumbnails).

## CLI (still available, for local single-video use outside the web app)

```
ffmpeg  -> mono audio
Groq    -> timestamped transcript  (whisper-large-v3-turbo, free)
Groq/Claude -> ranked standalone clips  (structured JSON)
ffmpeg  -> cut each clip
```

```powershell
python clipfinder.py path\to\video.mp4
python clipfinder.py path\to\folder --n 8
python clipfinder.py video.mp4 --taste "chaotic reactions, quotable lines; skip slow talking" --vertical --picker claude
python clipfinder.py video.mp4 --dry-run   # just see the picks, don't cut
```

| flag | effect |
|---|---|
| `--n 10` | clips per video |
| `--taste "..."` | free-text editing preference passed to the picker |
| `--picker groq` (default, free) / `--picker claude` | which LLM ranks the clips |
| `--model` | Claude model when `--picker claude` — `claude-opus-5` (default) · `claude-sonnet-5` · `claude-haiku-4-5` |
| `--vertical` | also render a 1080×1920 centre crop |
| `--vision` | Claude looks at one frame per clip and scores it 1-5 (needs `ANTHROPIC_API_KEY`) |
| `--dry-run` | transcript + picks only, no cutting |

Output: `<video folder>/clips/<video name>/NN_title-slug.mp4` + `clips.json`.

Clips inherit the source resolution — the tool never upscales a horizontal clip, and
`--vertical` output is forced to 1080×1920, so a low-res source looks soft cropped.
