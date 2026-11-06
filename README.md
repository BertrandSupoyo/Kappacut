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

## The AI agent page

`/app/agent` is a chat for working out what to do with a video. People ask questions, or drop a video
in, and it helps with what to cut, how to frame it and what to post first. A panel beside it shows the
workflow, from upload to render, worked out from the project's real state rather than the model's words.
It shares the AI editor's tools below, and adds a few that only put something in front of the person:
`suggest_clips` shows clips with Keep and Skip buttons, `propose_render` shows a Render button, and
`offer_choices` shows answers as buttons. None of them does anything until the person clicks, and the
Render button uses the same route and limits as the editor's.

A preview beside the chat follows the video the whole way: the file on the person's own computer while it
uploads, the full video while it's analyzed, then each clip as it'll look with the edits so far. That's the
editor's own player on the same state, so a change from the chat shows straight away. After a render it
switches to the real files. A format the browser can't play gets a still from the clip instead of a black box.

Measured with `tests/agent_eval.py`, using `--set page` and `--set page-holdout`:

| `openai/gpt-oss-120b` | Runs | Passed |
|---|---|---|
| **Held out:** 24 requests, each run 3 times | 72 | 72 |
| Tuning set: 54 requests, each run twice, used to find and fix bugs | 108 | 107 |

Don't read too much into the perfect held-out score. Earlier held-out runs missed 1 of 36, 2 of 33 and 2
of 69. Those requests led to fixes, so they moved to the tuning set and new ones took their place. One
new check was itself wrong on its first run, banning "2 GB" and so also "1.2 GB", and was corrected. The
video is made up, and the requests were written by the same hands as the agent.

The bigger test was the whole thing in a browser, on a throwaway copy of the stack: real uploads, real
transcription, the real model and real renders. That run isn't part of CI yet. It found what the
sets above couldn't:

- **The page crashed on load.** A state selector returned a new array on every call, and React
  re-rendered until it gave up. A type check can't see that.
- **The card contradicted the reply.** Real clips are stored in time order, but the eval's happened to
  be best first. The model passed "kept", so it said "clip 2 first" above a card starting with clip 1.
  `suggest_clips` now takes numbers only, and the eval covers clips stored the real way.
- **Wrong numbers.** Given 1200 of 5120 MB, the model said "3 GB used". `my_usage` now hands over
  finished sentences, so there's nothing left to work out.
- **Claiming a change that didn't happen.** Analyses often start clips vertical with bold captions, so
  the offer to make them that way did nothing, and the model still said "now set". The offer now
  depends on the clips' real state, and a tool that changed nothing says so.
- **A session running out.** The chat printed the server's "Unauthorized" when a session ended.
  The next person to sign in on that tab could also see the chat and project names. People now get a plain
  explanation and their message back, and the page reloads when a different person signs in.
- **A warning the model ignored.** Asked to repeat a change it had just made, it said "now applied" in 6 of
  10 runs. The tool told it nothing had changed, but beside its own "done". The note now replaces that
  answer: 14 of 14. A similar check with no earlier conversation had passed 10 of 10, so only the browser
  run's long chat showed this up.

### How it talks

It's meant to sound like a colleague, not a manual. Ten held-out conversation requests, such as small
talk, thanks, "am I talking to a bot?", and Indonesian and Spanish, scored 15 of 20 on the old prompt and
30 of 30 on the new one. The old prompt was scored blind, and only its total was looked at. These checks
are crude: length, no tools, no claim to be human. They catch a lecture, not whether a reply is pleasant
to read.

Working on the tone found bugs, and caused some:

- **A failed message came back.** After a request failed, its question stayed in the history as if still
  waiting, so saying "hi" got an answer to it. Failed messages are no longer sent. The model given such a
  history directly still often answers the old question, which is the one tuning miss above, so this is
  fixed in the code and not by the prompt.
- **An opinion changed a clip.** "Clip 1 feels a bit boring to me" skipped clip 1 without being asked, in 2
  of 2 runs. It now asks first, in 10 of 10.
- **Old facts stated as fact.** The model is sure YouTube Shorts stop at 60 seconds, which stopped being
  true in October 2024. It now sends people to the platform's help pages rather than quoting a limit.
- **Friendlier rules broke working ones.** The first version of those rules refused to state Kappacut's
  own 2 GB limit, re-kept skipped clips so it could offer a render, and showed three clips when asked for
  the best one. Only the older tests caught that, which is why they run after any prompt change.

The count moves a little between runs. Other runs of the same set also missed a reassurance with no next
step, a good answer over the word limit, and describing a clip instead of pointing to the preview.

## The AI editor

A panel in the editor takes plain requests: "cut 2 seconds off the start of this clip", "bold
captions on every kept clip", "end clip 3 after she says that changed everything". It reads the
transcript if it needs to, changes the clips, lists exactly what changed, and offers one Undo.

- **A tool-calling loop** (`app/agent.py`) with eleven tools (`app/agent_tools.py`). Each tool has
  the limits of the slider it stands in for, and an impossible request comes back as an error the
  model can read and correct.
- **It never saves anything.** The browser holds the live edits and autosaves them, so the agent
  changes a copy and returns field-level changes, applied as one undoable step. Anything else you
  change meanwhile survives. If a request fails partway, nothing changes.
- **The list of changes comes from the edits, not the model.** It compares before and after, so
  a model that claims a change it didn't make can't mislead anyone.
- **A transcript is data.** Speech in a video can say "ignore your instructions". No tool can
  render, delete, spend or reach another project, so the worst it can cause is an edit you can see
  and undo.
- **Limits:** `AGENT_DAILY_CAP` messages a day per user (admins have none), 20 a minute, 6 model
  calls a message. It reads text only: it can't see the picture. What's typed isn't stored or logged.

`tests/test_agent.py` covers the tools and the loop with a scripted model. `tests/agent_eval.py`
sends real requests to a real model and checks the edits that come out, including that nothing else
was touched. Re-run it before changing the model, prompt or tools.

| `openai/gpt-oss-120b` | Runs | Passed |
|---|---|---|
| **Held out:** 15 requests written after tuning, each run 3 times, never used to change anything | 45 | 45 |
| Tuning set: 37 requests, used to find and fix bugs, so this one flatters | 37 | 36 (97%) |

The first run scored 71%, a few of those failures being the test's own mistakes, and looking at why
is what made it worth having: "cut 3 seconds" cut 5.6 because the model repeated a move that had already worked, and Groq refused 10 of 12 tries at
"remove the sound effects" because the model sends `null` for optional settings. Both are fixed in
the tools. An earlier held-out run scored 46 of 48. The request behind that led to a small fix, so it moved
to the tuning set. The one tuning miss is a real weak spot: given three changes in one message, it did only
two in 4 of 8 runs and still said it had done all three. The list of changes stays right, because it comes
from the edits, but the sentence doesn't. The video is made up and the requests were written by the same hands as the agent, so these numbers
say whether it follows instructions like these, not whether the edits look good or how real people
will phrase things. A message takes about a second and 5,000 tokens.

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
