# clipfinder — Web App Plan

Turn the working `clipfinder.py` CLI into a local web application: upload a video →
see AI highlight recommendations → accept them or hand-trim your own cuts on a
timeline → play/download the rendered clips.

Each phase is self-contained and can be executed in a fresh chat with `/claude-mem:do`.

---

## Status going in

- `clipfinder.py` pipeline is **stable** — runs end to end, handles ffmpeg failures,
  Groq rate limits, garbled transcripts. Quality is source-limited (chaotic 360p ≈ 1-2
  strong clips/video) but that is not instability. Safe to build a UI on.
- Machine: `.venv-1` (Python 3.12) already has `anthropic`, `groq`, `pydantic`,
  `scenedetect`; `ffmpeg`/`ffprobe` on PATH. **Web deps are NOT installed yet** (Phase 0).
- Everything here is pure-Python + ffmpeg.exe — nothing Smart App Control blocks.

---

## Phase 0 — Discovery, allowed APIs, setup

### 0.1  Install web dependencies (into `.venv-1`)

```powershell
c:\Users\leosa\Downloads\Compressed\files\.venv-1\Scripts\python.exe -m pip install `
    fastapi "uvicorn[standard]" python-multipart
```

All three are pure-Python wheels (uvicorn[standard] pulls `httptools`/`websockets` —
still fine under SAC; if any native piece is blocked, drop `[standard]` → plain `uvicorn`).

### 0.2  `clipfinder.py` surface the web layer will reuse (verified — do NOT reinvent)

| Function | Line | Signature | Use |
|---|---|---|---|
| `load_config` | `clipfinder.py:100` | `(path: Path) -> dict` | load `clipfinder.toml` + CLI-less defaults |
| `load_dotenv` | `clipfinder.py:90` | `(path: Path) -> None` | load `.env` keys |
| `extract_audio` | `clipfinder.py:132` | `(video: Path, workdir: Path) -> Path` | video → mono mp3 |
| `transcribe` | `clipfinder.py:204` | `(mp3: Path, workdir: Path, cfg: dict) -> list[dict]` | Groq Whisper + rule filters; segments are `{"start","end","text"}` |
| `find_clips` | `clipfinder.py:598` | `(segments: list[dict], cfg: dict) -> tuple[list[Clip], usage|None]` | dispatch groq/claude picker + `_finalize` |
| `_coherence_filter` | `clipfinder.py:619` | `(clips, segments, cfg) -> list[Clip]` | drop word-salad clips |
| `vision_check` | `clipfinder.py:669` | `(video, clips, workdir, model) -> dict[int, VisualNote]` | Claude scores 1 frame/clip (needs `ANTHROPIC_API_KEY`) |
| `cut_clip` | `clipfinder.py:154` | `(video: Path, start: float, end: float, out: Path, vertical: bool) -> None` | **reuse verbatim for manual cuts** — arbitrary start/end |
| `probe_duration` | `clipfinder.py:126` | `(path: Path) -> float` | video length in seconds |
| `grab_frame` | `clipfinder.py:164` | `(video: Path, at: float, out: Path) -> None` | thumbnail at time `t` |
| `Clip` model | `clipfinder.py:251` | fields: `start_seconds, end_seconds, title, hook, why, quote, score` | already Pydantic → `.model_dump()` for JSON |

`process_video` (`clipfinder.py:699`) is the CLI's all-in-one; Phase 1 splits its body
into `analyze` (no cutting) + `render` (cutting only) — **copy its steps, do not import it**.

### 0.3  Allowed APIs (cite when unsure — do not invent)

- **FastAPI** (read on demand: https://fastapi.tiangolo.com/ ; installed version's
  `fastapi/__init__.py` for exact exports):
  - `FastAPI()`, `@app.get/@app.post`, `APIRouter`
  - `UploadFile`, `File(...)`, `Form(...)` — multipart upload (needs `python-multipart`)
  - `BackgroundTasks` — kick off analysis after responding
  - `fastapi.responses.StreamingResponse` — SSE progress (`media_type="text/event-stream"`)
  - `fastapi.responses.FileResponse` — serve a single mp4
  - `fastapi.staticfiles.StaticFiles` — mount `web/` and the media dir
  - `fastapi.concurrency.run_in_threadpool` — run ffmpeg / the pipeline OFF the event loop
- **Anthropic SDK** (only if `cfg["backend"]=="claude"` or vision on) — patterns already
  in `clipfinder.py`: `client.messages.parse(model=..., max_tokens=..., messages=[...],
  output_format=PydanticModel)` → `resp.parsed_output`, `resp.usage`. (Ref: the
  `claude-api` skill, `python/claude-api/README.md` §"Vision (Images)" and
  `tool-use.md` §"Structured Outputs".)
- **HTML5** `<video controls>` for playback; `<input type="range">` + pointer events for
  the trim scrubber (no external JS lib required).

### 0.4  Anti-patterns to guard against

- ❌ Re-implementing transcription / clip-finding / cutting in the web layer — call
  `clipfinder.py` functions.
- ❌ Running `ffmpeg` or `transcribe()` directly in an `async def` route — it blocks the
  loop. Always `await run_in_threadpool(...)` or use `BackgroundTasks`.
- ❌ Inventing FastAPI helpers (`app.stream`, `SSEResponse`, …) — only the names in 0.3.
- ❌ A frontend build step (webpack/vite/react) — the user asked for a page; ship static
  `index.html` + `app.js` + `styles.css`, no bundler.
- ❌ Storing job state in a DB — a module-level `dict` keyed by `job_id` is enough for a
  local single-user app.
- ❌ `output_format=` field invented for Groq — Groq uses `response_format={"type":
  "json_object"}` + lenient parse (see `_groq_chat` at `clipfinder.py:486`).

### 0.5  Verification

- [ ] `python -c "import fastapi, uvicorn, multipart; print('web deps ok')"` succeeds in `.venv-1`
- [ ] `python -c "import clipfinder; print(clipfinder.find_clips, clipfinder.cut_clip)"` from
      the `clipfinder/` dir succeeds (module is importable, not just runnable)

---

## Phase 1 — Backend library layer (`pipeline.py`)

**Goal:** two callable entry points the web layer drives, plus a progress hook. No FastAPI here.

### What to implement

Create `clipfinder/pipeline.py`:

1. `analyze(video_path: Path, cfg: dict, on_progress=lambda pct, msg: None) -> dict`
   - Copy the step sequence from `process_video` (`clipfinder.py:699-740`):
     `extract_audio` → `transcribe` → `find_clips` → `_coherence_filter` → (optional
     `vision_check` when `cfg["vision"]`).
   - **Do not cut anything.** Return:
     ```python
     {
       "duration": float,                     # probe_duration(video_path)
       "segments": [{"start","end","text"}],  # from transcribe()
       "clips": [ {**clip.model_dump(),
                   "duration": round(end-start,1),
                   "visual_score": int|None} ],   # the recommendations
     }
     ```
   - Call `on_progress(10, "extracting audio")`, `(35,"transcribing")`,
     `(70,"finding highlights")`, `(90,"checking coherence")`, `(100,"done")`.
   - Use a `tempfile.TemporaryDirectory()` for the workdir exactly like `process_video`.

2. `render(video_path: Path, ranges: list[dict], cfg: dict, out_dir: Path) -> list[dict]`
   - `ranges` items: `{"start": float, "end": float, "vertical": bool, "label": str}`.
   - For each: `cut_clip(video_path, start, end, out_dir / f"{i:02d}_{slugify(label)}.mp4",
     vertical)` (reuse `clipfinder.slugify` at `clipfinder.py:694`).
   - Return `[{"file": name, "start", "end", "duration"}]`.

3. Keep `cfg` loading centralised: `def get_cfg() -> dict` that does
   `load_dotenv(HERE/".env")` + `load_config(HERE/"clipfinder.toml")` and sets
   `cfg["dry_run"]=False`, `cfg["out"]=""` (fields `process_video` expects).

### Documentation references

- Step order & tempdir usage: `clipfinder.py:699-776` (`process_video`).
- `Clip.model_dump()` shape: `clipfinder.py:251-276`.
- `cut_clip` is already arbitrary-range and vertical-aware: `clipfinder.py:154-162`.

### Verification checklist

- [ ] `from pipeline import analyze, render` works from `clipfinder/`
- [ ] `analyze(Path("<short test mp4>"), get_cfg())` returns a dict with non-empty
      `segments` and a `clips` list; prints the 5 progress callbacks in order
- [ ] `render(<mp4>, [{"start":5,"end":15,"vertical":False,"label":"test"}], cfg, tmp)`
      produces a playable `00_test.mp4` (`ffprobe` shows ~10s)
- [ ] `grep -n "subprocess\|WhisperModel\|response_format" pipeline.py` → **no matches**
      (all heavy lifting delegated to `clipfinder.py`)

### Anti-pattern guards

- Do not duplicate `transcribe`/`find_clips` logic. `pipeline.py` should be < 120 lines.
- Do not swallow exceptions — let `analyze`/`render` raise; the web layer maps to HTTP 500.

---

## Phase 2 — FastAPI server (`server.py`)

**Goal:** HTTP surface: upload, background analyze, SSE progress, results, render, media.

### What to implement

Create `clipfinder/server.py`:

```
JOBS: dict[str, dict] = {}          # job_id -> {status, pct, msg, result, error, video}
DATA = HERE / "web_data"            # uploads + rendered clips live here
```

Endpoints (all names/types from Phase 0.3):

| Method | Path | Body / params | Returns |
|---|---|---|---|
| `POST` | `/api/upload` | `UploadFile` | `{"job_id"}` — saves to `DATA/<job_id>/source<ext>`, sets `JOBS[job_id]={status:"queued",...}`, schedules `BackgroundTasks` → `_run_analyze(job_id)` |
| `GET` | `/api/jobs/{job_id}` | — | full job dict (status `queued`/`running`/`done`/`error`, `pct`, `msg`, and `result` when done) |
| `GET` | `/api/jobs/{job_id}/events` | — | `StreamingResponse(media_type="text/event-stream")` — yields `data: {json}\n\n` every ~0.5s until status in `{done,error}` |
| `POST` | `/api/jobs/{job_id}/render` | `{"ranges":[{start,end,vertical,label}]}` | runs `pipeline.render` via `run_in_threadpool`; returns `{"clips":[{file,url,start,end,duration}]}` where `url = /media/{job_id}/clips/{file}` |
| `GET` | `/media/{job_id}/source` | — | `FileResponse` of the uploaded video (for the editor `<video>`) |
| `GET` | `/media/{job_id}/clips/{name}` | — | `FileResponse` of a rendered clip |
| `GET` | `/` and static | — | `StaticFiles(directory=HERE/"web", html=True)` mounted at `/` |

`_run_analyze(job_id)`:
```python
def _run_analyze(job_id):
    j = JOBS[job_id]; j["status"] = "running"
    try:
        j["result"] = analyze(Path(j["video"]), get_cfg(),
                               on_progress=lambda p,m: j.update(pct=p, msg=m))
        j["status"] = "done"
    except Exception as e:
        j["status"], j["error"] = "error", str(e)
```

Run it: `uvicorn server:app --port 8000` (add a `if __name__=="__main__": uvicorn.run(...)`).

### Documentation references

- `UploadFile` + `python-multipart`: FastAPI docs "Request Files".
- SSE via `StreamingResponse`: FastAPI docs "Custom Response" — generator yielding
  `f"data: {json.dumps(payload)}\n\n"`, `media_type="text/event-stream"`.
- `run_in_threadpool`: `from fastapi.concurrency import run_in_threadpool`.
- `StaticFiles(..., html=True)`: FastAPI docs "Static Files".

### Verification checklist

- [ ] `uvicorn server:app` starts without error; `GET /` serves an HTML page (even a stub)
- [ ] `curl -F "file=@<short.mp4>" localhost:8000/api/upload` → `{"job_id": "..."}`
- [ ] `curl localhost:8000/api/jobs/<id>/events` streams `data: {...}` lines and ends at `done`
- [ ] `GET /api/jobs/<id>` after done has `result.clips` (list, may be short) and `result.segments`
- [ ] `POST /api/jobs/<id>/render` with one range returns a `url` that plays in a browser
- [ ] `grep -n "def transcribe\|ffmpeg\|Groq(" server.py` → no matches (delegates to `pipeline`)

### Anti-pattern guards

- ❌ `async def _run_analyze` — it's a sync background task; keep it `def`.
- ❌ Blocking `time.sleep` in the SSE generator on the main thread — use `await asyncio.sleep(0.5)`.
- ❌ Path traversal on `/media/{job_id}/clips/{name}` — validate `name` with
  `Path(name).name == name` before joining.

---

## Phase 3 — Frontend shell (`web/index.html`, `web/styles.css`)

**Goal:** the glassmorphism look, animated background, testimonials, page skeleton — no
data wiring yet.

### What to implement

`web/index.html` — single page, three sections:
1. **Hero** — product name, one-line pitch, an upload dropzone (`<input type="file" accept="video/*">` styled as a glass card).
2. **Workspace** (hidden until a job starts) — left: recommendations list; center: video + trim timeline; right: rendered-clips gallery.
3. **Testimonials** — 3-4 glass cards with quote + name + role (static content, provided
   in the file).

`web/styles.css`:
- **Glassmorphism tokens** on `:root`: `--glass-bg: rgba(255,255,255,0.08)`,
  `--glass-border: rgba(255,255,255,0.18)`, `backdrop-filter: blur(16px) saturate(140%)`,
  soft shadow, 16-20px radius. Provide a `.glass` utility class.
- **Moving background**: 2-3 large blurred radial-gradient "blobs" in absolutely-positioned
  divs behind everything, animated with `@keyframes` `translate`/`scale` over 20-40s,
  `will-change: transform`. Respect `@media (prefers-reduced-motion: reduce)` → freeze.
- **Theme-aware**: define the full palette on bare `:root` (dark-first is fine here);
  redefine under `@media (prefers-color-scheme: light)`. Body gets an explicit gradient
  background so nothing is transparent.
- Responsive: workspace is a 3-column grid ≥1100px, stacks to 1 column below. Wide
  content (timeline) scrolls inside its own `overflow-x:auto`.

### Documentation references

- Glassmorphism = `backdrop-filter` — no library. MDN `backdrop-filter`.
- Keyframe blob background — pure CSS, no JS.

### Verification checklist

- [ ] Open `web/index.html` directly (file://) — hero + testimonials render, blobs animate
- [ ] Toggle OS dark/light — palette flips, text stays readable, nothing goes transparent
- [ ] Set "reduce motion" in OS — blobs stop animating
- [ ] Page body never scrolls horizontally at 375px / 768px / 1440px widths

### Anti-pattern guards

- ❌ Loading Tailwind/Bootstrap/React from a CDN — hand-write the CSS.
- ❌ A single 4000-line file — split `index.html` / `styles.css` / `app.js`.

---

## Phase 4 — Frontend flow (`web/app.js`): upload → progress → results

**Goal:** wire the shell to the API. No manual-trim editor yet (Phase 5).

### What to implement

`web/app.js` (vanilla, ES modules or one IIFE):

1. **Upload**: on file pick → `POST /api/upload` (FormData) → get `job_id` → reveal the
   Workspace section → open `new EventSource('/api/jobs/'+job_id+'/events')`.
2. **Progress**: on each SSE message, update a glass progress bar (`pct` + `msg`). On
   `status==="done"` close the EventSource and `GET /api/jobs/{id}` for the full result.
   On `"error"` show the error in a glass toast.
3. **Recommendations panel**: render `result.clips` as glass rows — `MM:SS – MM:SS`,
   `title`, `hook`, a score pill, `visual_score` pill if present. Each row has
   **"Load in editor"** (Phase 5 hook — for now just store the range) and a checkbox.
4. **Render**: a "Render checked" button → `POST /api/jobs/{id}/render` with the checked
   ranges (`{start,end,vertical:false,label:title}`) → on response, append `<video controls
   src=url>` cards to the gallery, each with a download link (`<a href=url download>`).
5. Helper `fmt(t)` → `M:SS`. Keep all fetch calls in one `api` object.

### Documentation references

- `EventSource` — MDN, built-in, no polyfill.
- `FormData` + `fetch` for the upload — MDN.

### Verification checklist

- [ ] Pick a short mp4 → progress bar advances through the 5 messages → recommendations
      list appears
- [ ] Check 1-2 recommendations → Render → clip `<video>` cards appear and **play**
- [ ] Download link saves the mp4
- [ ] Network tab: exactly one `/api/upload`, one EventSource, one `/render` per action

### Anti-pattern guards

- ❌ Polling `/api/jobs/{id}` in a `setInterval` — use the SSE stream (fall back to one
  poll only if `EventSource` errors).
- ❌ Rebuilding the whole DOM on every SSE tick — update only the progress node.

---

## Phase 5 — Manual trim editor with highlight markers

**Goal:** the user's requested feature — hand-pick a cut on a timeline, with the AI
highlights shown as markers/suggestions.

### What to implement

In `web/index.html` center column + `web/app.js`:

1. **Video**: `<video id="editor" src="/media/{job_id}/source" controls preload="metadata">`.
2. **Timeline** under the video (a `position:relative` bar, width 100%):
   - A **playhead** that follows `video.currentTime` (`timeupdate` event).
   - **Highlight markers**: for each `result.clips[i]`, an absolutely-positioned glass
     segment from `start/duration*100%` to `end/duration*100%`, tinted by score. Click a
     marker → set selection to that range + `video.currentTime = start`.
   - **Two draggable handles** (in / out) — pointer events (`pointerdown`/`pointermove`/
     `pointerup` with `setPointerCapture`). Dragging updates `sel.start` / `sel.end`
     (clamp to `[0, duration]`, keep `end-start` ≥ `min_seconds` from a `/api/config`
     value or hardcode 3s). Show `sel` as `M:SS – M:SS` and its duration.
   - Buttons: **"Set in = playhead"**, **"Set out = playhead"**, **"Preview"**
     (`video.currentTime = sel.start`, play, pause at `sel.end` via a `timeupdate` guard).
3. **"Add this cut"** → pushes `{start:sel.start, end:sel.end, vertical:<toggle>,
   label:"manual"}` into the render list (same list Phase 4's checkboxes feed). A small
   list shows queued cuts (AI-accepted + manual) with remove buttons.
4. **"Render all"** → the Phase 4 render call with the combined list.

### Documentation references

- Pointer events + `setPointerCapture` for drag handles — MDN "Pointer events".
- `HTMLMediaElement.currentTime`, `timeupdate`, `loadedmetadata` — MDN.
- Reuse `cut_clip` server-side — no new backend code, `/render` already takes arbitrary ranges.

### Verification checklist

- [ ] Markers appear at the recommended timestamps, tinted by score
- [ ] Click a marker → video seeks there, selection snaps to that range
- [ ] Drag in/out handles → selection updates, can't invert or go sub-3s
- [ ] "Set in/out = playhead" works while the video plays
- [ ] "Preview" plays only the selected span and stops
- [ ] Queue an AI clip + a hand-drawn clip → "Render all" → both render and play in the gallery
- [ ] Vertical toggle on a manual cut → rendered file is 1080×1920 (`ffprobe`)

### Anti-pattern guards

- ❌ A drag library (interact.js, …) — pointer events are ~40 lines.
- ❌ Recomputing marker positions on every `timeupdate` — position them once on
  `loadedmetadata`, only move the playhead per tick.
- ❌ Trusting client ranges blindly server-side — clamp `start`/`end` to
  `[0, probe_duration]` and `end>start` in `/render`.

---

## Phase 6 — Verification (end to end)

1. Fresh `.venv-1`, `uvicorn server:app`, open `http://localhost:8000`.
2. Upload `FUNNIEST IRL STREAMING MOMENTS BEST OF 2025.mp4` (short, already local).
3. Watch progress complete; confirm the recommendations list is populated.
4. Accept one recommendation (checkbox).
5. In the editor: click a highlight marker, nudge the out-handle +2s, "Add this cut".
6. Draw one fully manual cut elsewhere, toggle vertical, "Add this cut".
7. "Render all" → 3 clips appear, all **play** in the gallery, all downloadable.
8. `ffprobe` the vertical one → 1080×1920; the others → source resolution.
9. Grep sweep for anti-patterns:
   - `grep -rn "transcribe\|WhisperModel\|libx264" server.py pipeline.py` → only
     `pipeline.py` may reference `clipfinder`-level names via import; no reimplementation.
   - `grep -rn "cdn\|unpkg\|tailwind\|react" web/` → no matches.
   - `grep -rn "setInterval" web/app.js` → at most one (SSE fallback), commented as such.
10. Kill the server, restart, re-open a finished job's page → gallery still lists the
    rendered clips from `web_data/<job_id>/clips/` (server rebuilds `JOBS` lazily from
    disk on `GET /api/jobs/{id}` if the id folder exists — small nicety, optional).

### Deliverables

```
clipfinder/
  clipfinder.py         (unchanged - the engine)
  clipfinder.toml
  pipeline.py           Phase 1
  server.py             Phase 2
  web/
    index.html          Phase 3 + 5 markup
    styles.css          Phase 3
    app.js              Phase 4 + 5
  web_data/<job_id>/    uploads + clips (gitignored)
```

Add `web_data/` to `.gitignore`.

---

## Notes / decisions

- **Single-user local app** — no auth, no DB, in-memory job dict, CORS not needed (same origin).
- **Picker/vision** stay controlled by `clipfinder.toml` (`backend`, `vision`). The web UI
  can expose a settings drawer later; not in this plan. If `backend="claude"` or
  `vision=true` and `ANTHROPIC_API_KEY` is missing, `analyze` raises → job shows the error.
- **Rate limits**: on the free Groq tier `analyze` can pause 60-500s. The SSE `msg` should
  surface "waiting on free-tier rate limit" (pipeline already prints it — pass a
  `on_progress` message through when `_groq_chat` sleeps; small hook to add in Phase 1).
- **Quality ceiling is unchanged** — the web app makes the human-in-the-loop step (accept /
  hand-trim) fast, which is the right answer to "1-2 auto clips/video".
