# clipfinder

Find short-form clips in long videos with Whisper + Claude. No training, no local ML,
nothing Smart App Control blocks.

## Web app

```powershell
$py = "..\.venv-1\Scripts\python.exe"
& $py -m uvicorn server:app --port 8000
# open http://127.0.0.1:8000
```

Upload a video → watch it transcribe and rank highlights → on the timeline, click a
highlight to load its range or drag the two handles for your own in/out → edit the
auto-captions and pick a style (Clean / Bold / Box / Pop · bottom/center/top · size) with
a live preview on the player → queue AI + hand cuts together → **Render** → play /
download the clips (captions burned in).

Keyboard: `space` play/pause · `i`/`o` set in/out · `k` preview selection · `←`/`→` step
(shift = 5 s) · `Ctrl/⌘+Enter` add cut.

Uploads and clips live in `web_data/<job id>/` (gitignored). Files: `server.py` (FastAPI),
`pipeline.py` (`analyze` / `render` / `build_ass` around `clipfinder.py`), `web/` (static
UI). Design mockups: `Main.dc.html` / `Workspace.dc.html`; build plan: `PLAN.md`.

## CLI

```
ffmpeg  -> mono audio
Groq    -> timestamped transcript  (whisper-large-v3-turbo, free)
Claude  -> ranked standalone clips  (structured JSON)
ffmpeg  -> cut each clip
```

## Setup

1. Keys in `.env` (already has the Groq key; add the Anthropic one):

   ```
   GROQ_API_KEY=gsk_...          # console.groq.com   (free)
   ANTHROPIC_API_KEY=sk-ant-...  # console.anthropic.com
   ```

2. Install (into the existing `.venv-1`):

   ```powershell
   ..\.venv-1\Scripts\python.exe -m pip install anthropic groq
   ```

## Run

```powershell
$py = "..\.venv-1\Scripts\python.exe"

# one video
& $py clipfinder.py "C:\Users\leosa\Downloads\Video\streamer\IShowSpeed Most Random Moments!.mp4"

# whole folder, 8 clips each
& $py clipfinder.py "C:\Users\leosa\Downloads\Video\streamer" --n 8

# tune it
& $py clipfinder.py video.mp4 --taste "chaotic reactions, quotable lines; skip slow talking" `
      --vertical --vision --model claude-sonnet-5

# just see the picks, don't cut
& $py clipfinder.py video.mp4 --dry-run
```

## Output

`<video folder>/clips/<video name>/`
- `NN_title-slug.mp4` — the cut clips
- `clips.json` — start/end, title, hook, quote, score (+ visual score with `--vision`)

## Flags

| flag | effect |
|---|---|
| `--n 10` | clips per video |
| `--taste "..."` | free-text editing preference, passed to Claude |
| `--model` | `claude-opus-5` (default) · `claude-sonnet-5` · `claude-haiku-4-5` |
| `--vertical` | also render a 1080x1920 centre crop |
| `--vision` | Claude looks at one frame per clip and scores it 1-5 |
| `--dry-run` | transcript + picks only, no cutting |

## Picker: Groq (default, free) vs Claude

- `--picker groq` (default) uses `openai/gpt-oss-120b` on Groq's free tier. **No cost.**
  The free tier is 8000 tokens/minute, so the transcript is processed in ~4-minute
  windows and the run pauses ~60s whenever it hits the limit - a 30-min video takes a
  few minutes of mostly waiting. Only `GROQ_API_KEY` is needed.
- `--picker claude` uses Claude (`--model claude-opus-5` default). Better judgement on
  hooks / payoffs, one shot over the whole transcript, ~$0.02-0.10 per video. Needs
  `ANTHROPIC_API_KEY` with billing set up.

`--vision` (Claude looks at one frame per clip) always needs `ANTHROPIC_API_KEY`.

## Cost

| step | groq picker | claude picker |
|---|---|---|
| transcription (Groq Whisper) | free | free |
| clip selection | free | ~$0.02 (haiku) / ~$0.04 (sonnet) / ~$0.10 (opus) per ~30-min video |

## Note on source quality

Clips inherit the source resolution - the tool never upscales a horizontal clip (a
360p source stays 360p). `--vertical` output is forced to 1080x1920 for the platforms,
so a low-res source will look soft cropped to vertical.
