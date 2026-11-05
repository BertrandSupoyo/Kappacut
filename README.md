# Kappacut

Find short-form clips in long videos — transcribe with Groq Whisper, pick the best
moments with an LLM (Groq free tier by default, Claude optionally), then reframe,
caption, color-grade and render them. Self-hosted, multi-user, with open or invite-only
signup. English-language videos only for now: other languages are stopped before they use any
minutes, and the user can choose to analyze them anyway.

Before putting it online, read [DEPLOY.md](DEPLOY.md), including "Before you let anyone in".

## Architecture

FastAPI (`app/`) + async SQLAlchemy + Postgres + Redis + an `arq` worker + a React/Vite
frontend (`frontend/`), served behind Caddy with resumable uploads via `tusd`. Everything
runs as one Docker Compose stack (`compose.yaml`). The core engine (`kappacut.py`,
wrapped by `pipeline.py`) is unchanged from Kappacut's original single-user design —
the app around it handles accounts, quotas, job queuing and multi-tenant storage.

- `kappacut.py` — transcribe, pick clips (Groq or Claude), verify/refine, cut, caption
- `pipeline.py` — thin `analyze()`/`render()` wrapper the web app calls
- `app/` — FastAPI app: auth (cookie-based, fastapi-users), projects/jobs/editor routes,
  quotas, the credit ledger, AI thumbnails, the arq worker
- `frontend/` — the SPA: project list, upload, the clip editor, render panel
- `alembic/` — DB migrations

## The clip-picking model

The default since 2026-10-25, set in `kappacut.toml` `[picker]` and mirrored in
`kappacut.py` `DEFAULTS`:

| Step | Model | Why |
|---|---|---|
| Genre detection | `openai/gpt-oss-20b` | one small classification call |
| Picking (one call per ~4-min transcript window) | `gpt-oss-20b` and `gpt-oss-120b`, alternating | Groq limits each model separately, so picking gets both models' per-minute and daily budgets; if one model's daily cap is spent, the rest of the video finishes on the other |
| Judging + ranking | `openai/gpt-oss-120b`, `reasoning_effort = "low"` | one call that re-scores every candidate together, so mixed pickers still rank as one pool; falls back to 20b if 120b's daily cap is spent |

Measured on the dev stack (paid Groq key, two stored videos, 25 calls, $0.058 total):

- **Speed:** an 8-min video analyses in 22.8 s end to end (was ~2.9 min on the free key with a
  20b-only picker). Picking a 19-min video takes ~12 s; the judge call 5–8 s.
- **Cost:** ~$0.055 per hour of video — ~$0.04 Whisper + ~$0.015 picking and judging.
- **Judge effort:** low beat medium. Medium used ~3× the output tokens, took 2–4× longer, broke
  more often (2 of 6 runs vs 1 of 6) and agreed with low on 67–83% of clips, so it bought no
  visible difference.

Before changing models, prompts or effort, re-run the same test — `tests/model_bench.py`, on
stored transcripts, so no Whisper cost; about $0.03 per video, hard spend cap — and keep the
change only if it wins. The run command is in the file's docstring.

## Measuring and training the picks

Superusers do this in **Admin > Training**: watch one of your own videos, mark every moment worth
posting, then finish to see how the AI compares. The AI's picks stay hidden until you finish, so
label a video before you open it in the editor. Labels and runs are in the database. Labelled
projects are kept by the retention job, and deleting a project deletes its label.

| Number | Question it answers |
|---|---|
| **Found** | Of the moments you marked, how many did the AI catch? This is the one to quote, because it shows what the AI missed. |
| **Good** | Of the AI's picks, how many sit on a moment you marked? |
| **Ranking** | When a video has good and bad picks, how often does a good one score higher? 50% is chance. |

These show how well the AI matches one person's taste, not how many views a clip gets.

**Run training** learns the six `SCORE_WEIGHTS` from your labels and Keep/Skip choices, then tests
them on videos they weren't trained on. It needs at least 10 videos that each have both a good
and a bad pick, and it only offers replacement weights if they clearly beat the current ones.
Adopting them is a one-line edit in `kappacut.py`; nothing changes on its own.

The same numbers are available from the command line, and make no AI calls:

```powershell
docker compose exec web python -m app.cli eval-picks      # against what users kept and rendered
docker compose exec web python -m app.cli eval-labels     # against moments marked by hand
docker compose exec web python -m app.cli train-weights   # learn the score weights
```

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
python kappacut.py path\to\video.mp4
python kappacut.py path\to\folder --n 8
python kappacut.py video.mp4 --taste "chaotic reactions, quotable lines; skip slow talking" --vertical --picker claude
python kappacut.py video.mp4 --dry-run   # just see the picks, don't cut
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
