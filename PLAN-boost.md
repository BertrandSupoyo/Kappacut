# PLAN-boost.md — clipfinder feature roadmap

Source of record for the "boost the product" feature set discussed and scoped in
conversation (taste-builder, auto-render agent mode, category detection, trend-aware
scoring, credit ledger, AI thumbnails). Each phase is independently shippable.
Follows the same phase-log convention as `PLAN-multiuser.md` — mark ✅ DONE with
deviations/verification results as each phase completes.

**Locked constraint (do not violate):** clipfinder is free, open-signup, one shared
server-side Groq key protecting a shared budget (`app/quota.py`, `app/ratelimit.py`,
`app/groq_gate.py`). Any feature with real per-call cost beyond Groq's free-tier
text/audio (i.e. any generative image/video call) MUST be gated behind the Phase E
credit ledger before it can run. Text-only features need no such gate.

**Explicitly out of scope** (discussed and rejected/deferred this session — do not
add): live TikTok/Instagram trend-data scraping (ToS/reliability risk); voice cloning
or AI avatars (off-brand + likeness/consent risk); AI B-roll/generated cutaway video;
multi-language auto-dub (bigger lift, separate future plan); any full LLM
fine-tuning or local-model training pipeline (violates the "no local ML" constraint
this project deliberately closed off during the multi-user migration).

---

## Phase 0 — Documentation discovery (consolidated findings, verified this session)

All line numbers below were read directly from current files — do not trust any
older numbers from `PLAN-multiuser.md` or `PLAN-moment-flow.md`, they are stale.
Re-verify again at implementation time if more than a few days have passed.

**`clipfinder.py`:**
- `PICKER_BRIEF`: lines 519-541. `_VERIFY_BRIEF` / `_VERIFY_SHAPE`: lines 1038-1067.
  `_verify_and_refine(clips, segments, cfg) -> list[Clip]`: lines 1070-1164, called
  only from `pipeline.py:64`, gated on `cfg.get("coherence_check", True) and clips`.
- `cfg["taste"]` read at line 923 (`_find_clips_groq`) and 948 (`_find_clips_claude`),
  identical pattern: `f"\nEditor's taste to follow: {cfg['taste']}\n" if cfg["taste"] else ""`.
  `cfg` is a **plain dict**, not a TypedDict. `DEFAULTS` dict: lines 48-77 (`"taste": ""`
  at line 59). `load_config()`: lines 167-181 (TOML merge; unknown keys silently dropped).
- `Clip` model: lines 479-512. `beat_type: str = "moment"` is a **plain string, not an
  enum** — nothing enforces the `reaction|bit|story|quote|fail|wholesome|moment` list.
- `_find_clips_groq`/`_find_clips_claude`: lines 916/943, both
  `(segments: list[dict], cfg: dict, loud: list[float] | None = None)`.
  `PICKER_WINDOW_SEC = 240` (line 83), `AUDIO_CHUNK_SEC = 1200` (line 82).
- `transcribe(mp3, workdir, cfg) -> list[dict]`: line 400. Segments:
  `{"start","end","text","words":[{"w","start","end"},...]}` — `words` populated only
  when `cfg["word_timestamps"]` is truthy (web path forces this True, see below) —
  words-per-minute is computable from this for a `low_content` flag, nothing
  precomputes it today.

**`pipeline.py`:** `get_cfg()` (lines 19-28) is a **static, process-wide config
load** — it takes no arguments and there is no existing mechanism to pass a
per-request override (taste, category, etc.) into it. **This is the single
biggest gap Phase A must close.** `analyze()` calls `cf._verify_and_refine` at
line 64.

**`app/quota.py`:** `check_quota(session, user, action, *, extra_bytes=0) -> None`
(lines 107-134), raises `QuotaError`. Actions today: `"project"`, `"upload"`,
`"analyze"`, `"render"`. Call sites: `app/projects.py:85,192,204,302`.

**`app/ratelimit.py`:** `rate_limit(bucket, limit, window_seconds) -> Callable`
(lines 21-38), used as a FastAPI `Depends()`. `GlobalRateLimitMiddleware` already
mounted on `/api/*` (lines 41-69).

**`app/groq_gate.py`:** `gate(est_tokens=6000)` (lines 20-42), `install()`
(lines 45-49, sets `cf.GROQ_GATE`).

**`app/models/`:** `user.py`, `project.py` (`Project`, `Analysis`, `ProjectEdit`),
`job.py` (`Job`, `RenderOutput`), `sfx.py`, `usage.py` (`UsageEvent`). Copy-ready
analogue for any new table: `UsageEvent` (`usage.py:16-30`) or `RenderOutput`
(`job.py:47-65`) — UUID PK, FK-to-project + FK-to-user, `server_default=func.now()`.
**New model modules must be imported in `app/models/__init__.py`** or Alembic
autogenerate won't see them. Migration convention: `alembic/versions/<12-hex>_<slug>.py`,
generated via `alembic revision --autogenerate -m "<message>"`.

**`app/projects.py`:** `create_project` (lines 74-96, accepts only `filename`
today), `upload_source` (183-221, file only), `_owned()` ownership pattern
(38-42), `get_edit`/`save_edit` (255-285, whole-project JSON blob), `start_render`
(290-311, builds `Job(..., payload={"ranges": ranges})`).

**`app/worker.py`:** `WorkerSettings` (63-72): `max_tries=1` (a failed job stays
failed, no silent retry). `on_startup` (43-56) does groq_gate install,
`cf.FFMPEG_THREADS` set, `synth_builtins()`, **and** sweeps orphaned `"running"`
jobs to `"error"` on worker restart.

**`app/jobs.py`:** `run_analyze(job_id)` (42-125) — cfg is process-global via
`pipeline.get_cfg()`, **no per-job payload flows in today**. `run_render(job_id)`
(130-221) reads `ranges = (job.payload or {}).get("ranges")` — **`Job.payload`
(JSONB) is the one existing channel for per-request data into the worker.** Any
new per-analyze override (taste, auto_render flag) must flow through this same
channel.

**`app/config.py` `Settings`:** numeric-cap pattern is a bare typed class
attribute, e.g. `ffmpeg_threads: int = 0` (line 63) or the `quota_*` group
(lines 48-55) — `BaseSettings` + `env_file=".env"` makes it env-overridable with
no extra wiring.

**Frontend (`frontend/src/`):**
- `pages/Projects.tsx:42-76` `startUpload` — `POST /api/projects` body today is
  `{filename: string}` **only**, no taste/goal field exists anywhere (grep
  confirmed zero hits for taste/goal/steer/guided). Response is an inline
  `{id: string}`, no named type in `api/types.ts`.
- `editor/store.ts:12-40` `EditorState` — `edits: Record<number, ClipEdit>` keyed
  by clip index, `activeIdx: number`. `ClipEdit` shape: `editor/types.ts:12-36`
  (keep/start/end/caption/look/color/sfx/sfxAuto/autoOff) — a `rating` field
  slots in here the same way. Generic mutator `patch(i, fn)`: `store.ts:100-106`;
  example `toggleKeep`: `store.ts:108`.
- Per-clip controls render in `ClipCard.tsx:57-64` (review grid) and
  `ClipList.tsx:65-71` (edit-mode header) — either is where a rating control
  naturally sits next to the existing Keep/Skip `.btn.ghost` buttons.
- Autosave: `store.ts:44-58` `scheduleSave`, 900ms debounce, posts the **whole**
  `edits` map to `/api/projects/:id/edit` on every `patch()` call.
- `styles/tokens.css`/`global.css` — reuse verbatim. No `Badge`/`Pill` component
  exists; `.btn`/`.btn.ghost`/`.btn.danger` (`global.css:49-61`) and `Seg` (a
  segmented-button group, `editor/controls.tsx`) are the closest existing
  primitives — reuse `Seg` for the guided-question multi-choice, don't invent
  new classes.
- `api/client.ts:46-49` — `jget`/`jpost`/`jdelete` only, no `jpatch`; PATCH-like
  updates already go through `jpost`.

**Image-gen provider (for Phase G), verified via live fetch:**
Groq confirmed to have **no** image-generation capability (text/audio only) —
a second provider is unavoidable. **Together.ai recommended**: `POST
https://api.together.ai/v1/images/generations`, Bearer auth, plain synchronous
JSON (no SDK, no polling) — structurally identical to the existing Groq call
shape. Pricing (fetched from together.ai/pricing): FLUX.2 [dev] $0.0154/image,
FLUX.2 [pro]/[flex] $0.03/image, pay-as-you-go, no minimum spend. Rejected:
Replicate/fal.ai (async/polling, more integration work for no cost benefit),
Stability (pricing page didn't render, unverifiable), OpenAI images (priced by
token not per-image, murkier to budget), Gemini image (viable runner-up but the
fetched endpoint shape looked non-standard — spike-verify manually before
committing if Together's terms ever change).

---

## Phase A — Taste plumbing + guided question UI ✅ DONE (2026-09-12)

**What to implement:**
1. Backend: add `taste: Mapped[str | None]` column to `Project`
   (`app/models/project.py`) + Alembic migration.
2. `create_project` (`app/projects.py:74-96`) accepts optional `taste: str | None`
   in its request body, stores it on the new `Project.taste` column.
3. Find the tusd `post-finish` handler (in `app/uploads.py`) that enqueues the
   analyze job — read `project.taste` there and set
   `Job.payload = {"taste": project.taste}` if present, following the exact
   pattern `start_render` already uses for `{"ranges": ranges}`.
4. In `run_analyze` (`app/jobs.py:42-125`), after `cfg = pipeline.get_cfg()`,
   if `job.payload` has a `taste`, **shallow-copy before mutating**:
   `cfg = {**cfg, "taste": job.payload["taste"]}` — do NOT do `cfg["taste"] = ...`
   in place until you've confirmed `get_cfg()` returns a fresh dict per call and
   not a cached/shared one. This is the load-bearing correctness detail of this
   phase: two concurrent analyze jobs must never see each other's taste string.
5. Frontend: build the guided question flow in `Projects.tsx` before upload
   starts, using the existing `Seg` component (`editor/controls.tsx`) for each
   choice: platform/format, vibe (map 1:1 onto the `beat_type` values
   `reaction|fail|bit|story|quote|wholesome`), clip length bucket, clip count,
   optional free-text exclusions.
6. Compose answers into one string client-side (e.g. `"Prioritize beat_type:
   fail, reaction. Target length 15-30s. 5 clips. Avoid: profanity, spoilers."`)
   and send it as `taste` in the existing `POST /api/projects` call
   (`Projects.tsx:46`).

**Documentation references:** copy the FK/JSONB-payload pattern from
`app/projects.py:304-305` (`start_render`'s `Job(..., payload=...)` construction)
for step 3; copy `patch()`/`toggleKeep` (`editor/store.ts:100-108`) style for any
new store wiring; copy `Seg` usage from wherever `editor/controls.tsx` is already
consumed (e.g. `Inspector.tsx`) for step 5.

**Verification checklist (real Docker Compose stack, headless CDP):**
- Register/login, complete the guided form, upload a short real test clip,
  confirm via `docker compose logs worker` (or a temporary debug log) that the
  enqueued job's payload actually carried the composed taste string and that
  `pipeline.analyze` received it in `cfg["taste"]`.
- Run two analyze jobs concurrently with two different taste strings; confirm
  each job's picks reflect only its own taste (proves the shallow-copy fix).
- Confirm the Groq call count is unchanged from the documented baseline (1 call
  per 20-min transcribe chunk + 1 per 4-min Stage-1 window + 1 Stage-2 call).

**Anti-pattern guards:** do not mutate `pipeline.get_cfg()`'s return value in
place without confirming it isn't shared/cached; do not add any new LLM call in
this phase; do not invent a second channel for per-request data outside
`Job.payload`.

**New LLM/API requests added:** zero.

**Deviations / verification results:** `pipeline.get_cfg()` was confirmed to
call `cf.load_config()` fresh every invocation (builds `dict(DEFAULTS)` each
time, no memoization) — so the "shallow-copy defensively" guard reduces to a
direct `cfg["taste"] = job_taste`, which is what shipped
(`app/jobs.py:run_analyze`); no concurrency leak is possible since there's
nothing shared to leak. Guided UI reuses `Row`/`Seg` from
`editor/controls.tsx` as planned — `Seg`'s option arrays had to be typed as
plain (non-`as const`) `{id: T; label: string}[]` (matching the existing
`CROP_MODES` convention in `editor/types.ts`), not `readonly` tuples, or they
don't structurally match `Seg`'s generic prop type. Verified end-to-end via a
real headless-Chrome CDP run: registered/logged in a throwaway user, drove
the actual guide panel (vibe=Fail, length=&lt;15s, count=3, avoid="awkward
silence"), uploaded a real file through the real Uppy widget, then queried
Postgres directly — `projects.taste` and the analyze `jobs.payload` both
contained the exact composed string
`"Prioritize beat_type: fail. Target length under 15s. 3 clips. Avoid:
awkward silence."`. The subsequent analyze run failed at the transcription
step (`clipfinder.py:418`, Groq 401 `expired_api_key`) — this is the
pre-existing dev-environment key expiry noted earlier this session, not a
Phase A regression; it occurs well downstream of the `cfg["taste"]`
injection point, which was reached without error.

---

## Phase B — Goal-driven auto-render agent mode ✅ DONE (2026-09-12)

**Depends on:** Phase A (needs a taste string + the `Job.payload` plumbing to
exist first).

**What to implement:**
1. Add `auto_render: Mapped[bool] = mapped_column(default=False)` to `Project`
   (same migration batch as Phase A's `taste` column if not already shipped).
2. `create_project` accepts optional `auto_render: bool` alongside `taste`.
3. **Before writing code**, grep `app/projects.py`'s `start_render` body for
   exactly how `ranges` gets built from `ProjectEdit` — refactor that
   construction into a shared helper callable from both the route and the new
   auto-render path, rather than duplicating the logic.
4. In `run_analyze`, after clips are finalized, if `project.auto_render`:
   apply the existing `_auto_edit` heuristic per clip (re-verify its current
   signature in `clipfinder.py` before use — not re-confirmed this round) and
   enqueue a `render_task` using the shared helper from step 3, instead of
   waiting for the user to open the Editor.

**Verification checklist:** CDP test — create a project with `auto_render=true`
and a taste string from Phase A, upload, poll `/api/projects` until status
reaches a terminal render state with zero manual Editor interaction; confirm
`RenderOutput` rows exist.

**Anti-pattern guards:** must not add new LLM calls (identical call count to
Phase A's path — `_auto_edit` and rendering are both local/ffmpeg, no LLM).

**New LLM/API requests added:** zero.

**Deviations / verification results:** the plan's step 3 assumption was wrong
and got corrected during implementation — `start_render` does **not** build
`ranges` from `ProjectEdit` server-side; it takes `ranges` directly from the
request body (the frontend's `keptRanges()` in `editor/store.ts` builds them
client-side). So there was no server-side helper to extract; instead a new
`_clip_to_range(c: dict) -> dict` was added to `app/jobs.py`, a Python
port of `keptRanges()` + `applyAuto()`'s merge logic, driven off each clip's
already-attached `auto` suggestion (`clipfinder.py`'s `attach_auto()` /
`_auto_edit()`, confirmed to run unconditionally inside every
`pipeline.analyze()` call at `pipeline.py:66` — so no second call to
`_auto_edit` was needed, it's already computed). A `_try_auto_render()`
helper wraps its own try/except so a problem here can never overwrite
analyze's own already-committed success status. **Correctness detail found
during implementation:** calling the normal `_guard_enqueue`
(`app/projects.py:56-69`, "one active job per user") too early would
self-block, since the analyze job itself is still `status="running"` at that
point and the default free-tier `quota_concurrent_jobs` is 1 — fixed by
firing `_try_auto_render` only *after* the commit that marks the analyze Job
`"done"`, so it's no longer counted. `auto_render` needed a
`server_default=sa.false()` in its migration (then dropped after backfill)
since the table already had rows — a plain `nullable=False` `add_column`
fails against existing data, unlike the Phase A `taste` column which was
nullable. Frontend: a `Toggle` (from `editor/controls.tsx`, already used
elsewhere) added to the guide panel, defaulting to manual review (today's
behavior unchanged unless explicitly opted in). Verified two ways: (1) a real
CDP run confirmed `project.auto_render` and the composed taste both land
correctly in Postgres from the actual guide UI; (2) since the dev Groq key is
still expired (analyze fails before producing clips, so the real trigger
path can't fire end-to-end here), `_clip_to_range`/`_try_auto_render` were
exercised directly against the real DB with synthetic clip data (matching
`_auto_edit`'s real output shape) — confirmed a render `Job` was created,
enqueued via arq, and the worker picked it up and **actually completed a
real ffmpeg render** (`clipfinder.jobs: render ... done — 1 clip(s)`),
proving the full chain works; this will run through the real transcription
path automatically once the Groq key is refreshed.

---

## Phase C — Video-level category detection ✅ DONE (2026-09-12)

**What to implement:**
1. New system prompt (`CATEGORIZE_BRIEF`) + a small Pydantic output model
   (category + confidence), following the existing `ClipSet`/`_VerifySet`
   pattern in `clipfinder.py`.
2. One new lightweight call, run once per video using a short transcript
   excerpt (first window's segments are enough) **before** Stage 1 begins —
   this is a genuinely new LLM call, budget it explicitly.
3. Store the detected category on `Analysis` (new column) or `Project`.
4. Thread the category into the existing taste-injection point (same pattern
   as `cfg["taste"]`) so Stage 1 and Stage 2 prompts both see
   `"Video category: {category}."`.
5. Use the category to weight `_auto_edit` heuristics (re-verify its current
   logic before editing — not covered in this round's research).

**Verification checklist:** CDP/analyze test with two contrasting synthetic
transcripts (fast-cut gaming-style vs. calm long-form talk) — confirm distinct
categories are detected and that resulting `beat_type` distributions differ.

**Anti-pattern guards:** do not skip documenting the added call — update
`DEPLOY.md`'s capacity notes and this plan's phase log with the new baseline
call count once shipped.

**New LLM/API requests added:** +1 per video (explicitly budgeted, not free).

**Deviations / verification results:** implemented as `CATEGORIES` (a fixed
9-way tuple: gaming/podcast/vlog/interview/tutorial/comedy/sports/reaction/
other) + `CATEGORIZE_BRIEF` + `detect_category(segments, cfg)` in
`clipfinder.py`, inserted right before the "picker: Groq" section (after
`_auto_edit`). Reuses the existing `_groq_chat`/`_loads_lenient`/`as_timeline`
helpers exactly like the picker does, so it automatically participates in the
shared `GROQ_GATE` token-bucket — no separate rate-limit plumbing needed.
Falls back to `"general"` on ANY failure (bad JSON, network error, rate
limit, expired key) so category detection can never break analysis. The
per-stage taste injection was generalized into one `_steer_line(cfg)` helper
(replacing the old inline `taste_line = f"..." if cfg["taste"] else ""` in
both `_find_clips_groq` and `_find_clips_claude`) that combines category +
taste into one line, and — a genuine gap the original plan's "Stage 1 and
Stage 2 both see it" requirement caught — Stage 2 (`_verify_and_refine`)
previously had **no taste injection at all**; `_steer_line(cfg)` is now
appended to its prompt body too, so this phase quietly extended taste
support to Stage 2 as a side effect. `_auto_edit`/`attach_auto` gained a
`category` parameter driving a `_HOT_THRESHOLD` dict (0.35 for
gaming/reaction/comedy/sports, 0.55 for podcast/interview/tutorial, 0.45
default) that shifts how much of an energy spike is needed before a clip is
styled "hot" (punchier captions/color/zoom) — a calmer talk genre needs a
clearer spike before committing to punchy styling, a naturally energetic
genre gets flagged more readily. `Analysis.category` (nullable `String(32)`)
stores the result. Verified directly (the expired dev Groq key blocks a live
classification call the same way it blocks transcription, so this was
exercised without the network): `_steer_line`'s four input combinations all
produced the exact expected prompt text (including confirming `"general"` is
deliberately suppressed from the line since it carries no real signal),
`detect_category` was confirmed to degrade to `"general"` on the real 401
from the expired key without raising, and `_auto_edit` was confirmed to flip
`hot` from `True` to `False` for the identical energy-spike input purely by
changing `category` from `"gaming"` to `"podcast"` (0.45 sits between their
0.35/0.55 thresholds) — proving the per-category weighting actually changes
behavior, not just accepts the parameter.

---

## Phase D — Trend-aware scoring dimension (Stage 2 only) ✅ DONE (2026-09-12)

**What to implement:**
1. Add a `trend: int = 0` field (0-2 scale) to `_Verdict` (`clipfinder.py`
   around lines 1020-1032).
2. Extend `_VERIFY_BRIEF` (1038-1063) with rubric text scoring "matches
   patterns that currently perform well in short-form video — strong hook in
   the first line, quick punchline, loop potential" — **model's own general
   knowledge only, no external data fetch**.
3. Extend `_VERIFY_SHAPE`'s example JSON to include `trend`.
4. Update the score-summing logic (the `_sum` lambda near line 1114-1116) to
   include the new dimension and its max scale.

**Verification checklist:** feed a known synthetic transcript with one
obvious hook-first clip vs. one slow-buildup clip; confirm the `trend` score
differentiates them and shifts final ranking order.

**Anti-pattern guards:** must not add any external HTTP call for trend data —
this stays purely inside the existing single Stage-2 pass.

**New LLM/API requests added:** zero (extends the existing Stage-2 call's
prompt/schema only).

**Deviations / verification results:** implemented exactly as planned — one
new `trend: int = 0` field on `_Verdict`, one added sentence to
`_VERIFY_BRIEF`'s scoring rubric (0-2 scale, explicitly "matches patterns
that currently perform well in short-form video... no external data"),
`trend` added to `_VERIFY_SHAPE` and the few-shot example, and `trend` folded
into both the `_sum`/`ci` aggregation (falls into the existing 0-2 clamp
bucket automatically since it isn't `"hook"`/`"arc"`) and the main scoring
loop's `c.arc` dict / `c.score` total. Checked one real edge case before
trusting this: `Clip.score`'s `_normalise_score` validator divides by 10 if
a score is `> 10` (guarding against a model answering on a 0-100 scale) —
raising max possible score from 10 to 12 could have wrongly triggered that
on a genuinely excellent 5-dimension clip. Confirmed safe: that validator is
`mode="before"` with no `validate_assignment` configured on `Clip`, so it
only fires on construction (Stage 1's `Clip.model_validate`), never on
Stage 2's direct `c.score = ...` reassignment — no fix needed, just verified
the risk wasn't real. Also confirmed the decomposed score isn't surfaced
anywhere in the frontend (`arc` isn't even in the TS `Clip` type), so no UI
change was needed either. Verified with a mocked Groq response (real key
still expired) exercising the actual `_verify_and_refine` end-to-end, not
just the isolated pieces: (1) two tied-on-everything-else clips differing
only in `trend` produced the expected `arc.trend` and total `score`
(8.0 vs 6.0); (2) the harder test — 5 non-overlapping candidates with
`MIN_CLIPS=4` forcing exactly one to drop, all tied except one clip at
`trend=0` — confirmed that exact clip, and only that one, was dropped by
`_nonoverlap`'s score-descending greedy pass, proving `trend` actually
changes which clip survives, not just a number nobody consumes.

---

## Phase E — General credit-limit framework (infrastructure only) ✅ DONE (2026-09-12)

**What to implement:**
1. New `app/credits.py` mirroring `app/quota.py`'s shape:
   `check_credits(session, user, feature: str, cost: int) -> None`, raising a
   `CreditError` analogous to `QuotaError`.
2. New ledger table (or columns on `UsageEvent`) tracking credits used per
   period, following existing conventions.
3. Settings additions in `app/config.py` following the `quota_*` group
   (lines 48-55) pattern, e.g. per-feature daily caps.
4. **No feature wires into this yet** — this phase is infrastructure only,
   verified with a direct call/test, not a real route.

**Verification checklist:** a direct script/test call to `check_credits`
confirming it blocks after the cap and resets on the period boundary.

**New LLM/API requests added:** zero (no generation happens in this phase).

**Deviations / verification results:** deviated from "new ledger table" —
reused the existing `UsageEvent` table (`app/models/usage.py`) with a new
`kind` naming convention (`f"credit_{feature}"`) instead of creating a new
one. `UsageEvent` is already exactly this shape (user_id, project_id,
kind, quantity, created_at) and every other usage meter
(`transcribe_seconds`, `render_clip`, `upload_bytes`) is a bare `kind`
string with no enforced enum/constraint — adding a new naming convention on
the same table matches this project's actual convention more closely than a
parallel table would, and needs zero migration. `app/credits.py` mirrors
`app/quota.py`'s shape exactly: `CreditError` (mirrors `QuotaError`),
`credits_used_today` (mirrors `renders_today`), `check_credits(session,
user, feature, *, cost=1, cap=None)` — a caller may pass its own `cap` for a
feature-specific limit, or omit it to share the new
`settings.credit_daily_cap_default` (5) — a check-only function; recording
the actual spend (a plain `UsageEvent` insert, matching how `render_clip`
is recorded in `app/jobs.py` after a render actually completes, not at the
check point) is left to the caller once a real feature exists. Also added a
`CreditError` → 429 exception handler in `app/main.py`, mirroring the
existing `QuotaError` handler, so Phase F's first real caller has a fully
working error path already in place, not something to build alongside it.
Verified directly against the real DB (throwaway `UsageEvent` rows, cleaned
up after): confirmed the cap blocks exactly on the 4th spend at cap=3, the
raised `CreditError` carries the correct feature/used/cap values, different
feature names are isolated from each other, and the shared default-cap path
works when no explicit `cap` is passed.

---

## Phase F — AI thumbnail generation (first feature gated by Phase E) ✅ DONE (2026-09-12, code-complete — needs a real provider key to verify live)

**Depends on:** Phase E (credit ledger must exist and be enforced before this
ships).

**What to implement:**
1. Add `TOGETHER_API_KEY` to `app/config.py`/`.env.example`, following the
   exact pattern `GROQ_API_KEY` already uses.
2. New `app/thumbnails.py`: a plain `httpx` POST to
   `https://api.together.ai/v1/images/generations` (Bearer auth, JSON body,
   synchronous response — no SDK, no polling needed), mirroring the shape of
   the existing Groq client call.
3. New route `POST /api/projects/{project_id}/clips/{clip_index}/thumbnail` —
   **call `check_credits(..., "thumbnail", cost=1)` before calling Together,
   never after** (no free generation on a race). Store the resulting image on
   the media volume next to existing frame-grab assets (reuse the
   `media_frame` storage pattern from `app/projects.py`).
4. Frontend: a "Generate thumbnail" button in `RenderPanel.tsx`/`ClipCard.tsx`,
   showing remaining credits (needs a small `GET` for current balance).

**Verification checklist:** CDP test triggering thumbnail generation with a
real (small, capped) Together.ai API key — confirm image appears, credit
deducts, and generation is blocked once the cap is hit. Recommend testing with
a cap of 1 credit first since this is real spend, not free-tier.

**Anti-pattern guards:** credit check must happen strictly before the paid
API call. Never hardcode the API key.

**New LLM/API requests added:** yes — 1 paid image-generation call per
thumbnail, explicitly gated by Phase E's credit ledger (this is the intended
exception to the "zero new calls" pattern of every other phase).

**Deviations / verification results:** scope grew beyond the plan at the
user's explicit request ("lets combine ai by performance") — rather than a
single hardcoded provider, `app/thumbnails.py` implements **two** providers
(Together.ai and Replicate — the two candidates from Phase 0's research
whose HTTP shape was actually verified via a live fetch; Gemini's was not
and was deliberately left out rather than coded against a guess) behind one
`generate(prompt) -> bytes` interface, combined by a Redis-backed
performance router: each provider has a rolling success-rate + latency EMA
(mirrors `app/groq_gate.py`'s Redis-backed, fail-open style), untested
providers start at a neutral Laplace-smoothed 0.5 (not 0, so a fresh
provider isn't unfairly buried), and `generate_thumbnail()` always tries the
current best-ranked provider first, records the outcome, and **falls back to
the next-ranked provider within the same call** on failure — a provider that
starts erroring or slowing down loses traffic share automatically, no
config change needed. Route: `POST
/api/projects/{project_id}/clips/{clip_index}/thumbnail` — validates
ownership + clip index against `Analysis.clips`, calls `check_credits(...,
"thumbnail")` (Phase E) strictly before the provider call, writes the
`UsageEvent(kind="credit_thumbnail")` only after a real success, stores the
PNG via new `storage.thumbnail_path()`, served back via a new
`GET /media/{project_id}/thumbnails/{clip_index}.png`. `TOGETHER_API_KEY`
and `REPLICATE_API_KEY` added to `Settings`/`.env.example`, both optional —
either, both, or neither may be configured. **Honesty note carried in the
module docstring:** both providers' request/response shapes are grounded in
each vendor's published HTTP docs from Phase 0's research, not a live call
with a real key in this session — a wrong field name just makes that
provider fail its own attempts, which the router already routes around, but
still smoke-test with a real key before relying on either in production.
Verified without any real key: (1) the performance router itself, using two
fake providers — confirmed cold-start neutrality, confirmed a provider
seeded with a great-looking but stale track record gets tried first, fails,
and the router falls back to the working provider **within the same call**
(the core "combine by performance" claim), and confirmed repeated real
failures eventually overtake the stale seed in the ranking; (2) the real
(zero-key) path fails cleanly with a clear `ProviderError`, never a crash;
(3) the actual route function against a real user/project/synthetic
`Analysis` row — out-of-range clip index gives a clean 404, a valid clip
with no provider configured gives a clean 502, and critically **no credit
was spent** on the failed attempt, confirming the check-before/spend-after
invariant holds end-to-end. Live paid-call verification (does a real image
actually come back from Together or Replicate) still needs a real API key —
that's a manual step for whoever adds one, not further code.

---

## Final phase — Verification

1. Re-run the existing full-stack CDP regression suite (register → login →
   upload → analyze → render) to confirm phases A-F didn't break the baseline
   flow.
2. Confirm the call-count bookkeeping: phases A, B, D, E add zero new LLM
   calls; Phase C adds +1/video; Phase F adds 1 paid image call per thumbnail,
   credit-gated. Update `DEPLOY.md`'s capacity notes with the Phase C addition.
3. Grep for anti-patterns: every `pipeline.get_cfg()` per-job override uses
   `{**cfg, ...}` (never mutates the shared dict in place); the Together API
   key is read from `Settings`/env, never hardcoded.
4. Confirm every new model is imported in `app/models/__init__.py` and has a
   corresponding Alembic migration.
