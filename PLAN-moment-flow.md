# clipfinder — smarter moment-flow confirmation

Make the picker confirm each clip is **one complete beat** (setup → build → payoff → reaction),
not a fragment, not two moments stapled together, not cut off mid-thought. Grounded in:
Opus Clip (self-contained moment = hook + payoff), Rhapsody (transcript-primary, structured
prompts), PODTILE (running dynamic context across chunked transcripts), self-consistency
two-pass verification, chain-of-thought before scoring.

**Hard constraints (do not violate):** local / single-user / **no local ML / no new pip deps** /
must run on Groq free tier (`openai/gpt-oss-20b`) and optionally Claude. Every phase must
degrade to *today's* behaviour when a flag is off or a call fails.

Files in play: `clipfinder.py` · `pipeline.py` · `web/app.js`. Config lives in `DEFAULTS`
(clipfinder.py ~L48) + `clipfinder.toml` + web `_settings.json` overlay (`pipeline.get_cfg`).

---

## Phase 0 — Allowed APIs & existing patterns (read before touching code)

### LLM calls — copy the EXISTING shapes, do not introduce new SDK surface
- **Groq (free path)** — `groq==1.7.0`. clipfinder calls it as a **plain text prompt that ends
  with a JSON shape**, then parses with `_loads_lenient()` (clipfinder.py ~L676). Retry/backoff
  wrapper: `_groq_chat(client, model, prompt, temperature)` (~L693) — 4 tries, honours
  `retry-after`, `max_tokens=GROQ_PICK_MAX_TOKENS=5000`. **Anti-pattern:** do NOT switch the Groq
  path to `response_format={"type":"json_schema"}` even though 1.7.0 supports it — the 20b model's
  compliance is poor and `_loads_lenient` is the tested fallback. Keep prompt-shape + lenient parse.
- **Claude path** — `anthropic==1.3.0`. clipfinder uses `client.messages.parse(model=..., max_tokens=...,
  system=..., messages=[{"role":"user","content": ...}], output_format=<PydanticModel>)` and reads
  `resp.parsed_output` + `resp.usage` (see `_find_clips_claude` ~L827, `_coherence_filter` ~L884,
  `vision_check` ~L920). Copy this exactly for any new Claude structured call.
- **Whisper** — `client.audio.transcriptions.create(file=..., model=GROQ_WHISPER, response_format=
  "verbose_json", language="en", timestamp_granularities=["segment","word"])` returns segments with
  `text, start, end, avg_logprob, compression_ratio, no_speech_prob` and top-level `words`
  (transcribe ~L325). **These per-segment fields already exist — Phase 3 only needs to keep the
  timestamps of segments `_segment_action` currently drops.**

### Signals already computed (reuse, don't recompute)
- `loudness_track(audio) -> list[float]` — one RMS-level dB value per second (clipfinder.py ~L452).
- `_loud_stats(loud) -> {"p40","p75","p92"}` (~L473).
- `_clip_energy(c, loud) -> {"peak","payoff","peak_at","loud_frac"}` (~L481).
- `pipeline.analyze()` already: `loud = cf.loudness_track(mp3)` at 28 %, `find_clips(segments, cfg, loud)`
  at 65 %, `_coherence_filter(...)` at 85 %, per-clip `rec["auto"] = c._auto`.

### Data shapes
- `Clip` (clipfinder.py ~L391): fields `start_seconds, end_seconds, title, hook, why, quote, score`;
  PrivateAttrs `_energy: dict`, `_auto: dict`. Score validator normalises 0-100→0-10 and clamps 1-10.
  Text-field validator stringifies `None`. **Add new fields via the same model — do not attach loose
  attrs (pydantic v2 rejects undeclared non-private attrs).**
- `segments` = `list[{start, end, text, words?: [{w,start,end}]}]`, sorted by start.
- `as_timeline(segments) -> str` — `[MM:SS | 12.3s] text` one row per segment (~L381). This is the
  transcript format every picker prompt already uses. Reuse it.
- `_finalize(clips, segments, n, cfg, loud=None)` (~L629) is the single choke-point that snaps,
  trims, energy-adjusts, dedups (`_nonoverlap`), backfills to `MIN_CLIPS=4`, and sets `c._auto`.
- Boundary helpers: `_boundaries(segments)` → `(starts, ends, pauses)`; `_snap(value, points, max_shift)`;
  `_snap_clip(c, segments, cfg)` (~L519) already grows an edge to the enclosing sentence.

### `_coherence_filter` today (clipfinder.py ~L870) — what Phase 1 replaces
- Input: `list[Clip]`. Builds `lines = ['{i}: "{transcript}"' ...]`, ONE LLM call
  (Claude `.parse(output_format=CoherenceSet)` / Groq JSON prompt via `_groq_chat`), returns
  `[c for i,c in enumerate(clips) if verdicts.get(i, True)]`, `or clips` if all dropped.
- Called from **two** places: `pipeline.analyze()` (~L67) and CLI `process_video()` (clipfinder.py ~L972).
  Both must keep working.

### Anti-patterns to prevent
- Inventing Groq/Anthropic params. Only `model, messages, system, max_tokens, temperature,
  output_format` (Claude) / `response_format` untouched (Groq).
- Adding a pip dependency (numpy, librosa, whisper, torch, a VAD lib) — **banned**.
- A second Whisper pass or any per-frame video model — **banned** (SAC + cost).
- Breaking the CLI path (`process_video`) or the no-network `--dry-run`.
- Removing the `or clips` / backfill safety nets — never hand back an empty clip list.

---

## Phase 1 — Stage-2 "verify · refine · rank" pass  (replaces `_coherence_filter`)  ✅ CODE DONE

**Status:** implemented + component-verified in isolation (2 real-segment runs: refines bounds,
fills `arc`/`beat_type`/`hook_line`/`payoff_line`, never over-drops, holds the `MIN_CLIPS` floor).
Full end-to-end analysis run is pending — Groq free tier hit its **daily** token quota after
~10 test analyses (`retry-after: 417s`); a 3-window video re-hits it, so a clean full run needs
the quota to reset or an `ANTHROPIC_API_KEY` (Claude path is one fast call). **The user should run
one real analysis to confirm.**

What shipped:
- `Clip` gains `beat_type` (default `"moment"`), `hook_line`, `payoff_line`, `arc` dict; `_stringify`
  validator extended.
- `_verify_and_refine(clips, segments, cfg)` — one LLM call over every candidate with `_ctx_lines()`
  (±14/9 s tagged `[IN ]`/`[ctx ]`, middle-trimmed past 16 lines for the 8k-TPM tier). Constructive-first
  prompt (`_VERIFY_BRIEF`) with a one-shot example; scores hook(0-3)/arc(0-3)/quote(0-2)/standalone(0-2),
  tightens `start/end`, copies `hook_line`/`payoff_line`, labels `beat_type`. Groq: prompt-shape +
  `_loads_lenient` + a retry-if-empty loop (temp 0). Claude: `messages.parse(_VerifySet)`.
- Guards for a weak free model: if no verdict carries any score or `reason` → keep stage-1 picks
  unchanged; if the model rejects *everything* → treat `keep` flags as noise, rank by score only.
  A clip is a hard drop only when `trust_drops` (some signal + not all-rejected) and `keep=false`,
  or a scored `arc < 2`. Never below `max(MIN_CLIPS, count)` — tops up from a `weak` fallback pool.
- `_snap_to_lines(c, segments)` — pulls an edge to the exact segment carrying `hook_line`/`payoff_line`
  (normalised substring match, ≤10 s shift); additive, no-ops on no match.
- `_finalize` hands stage 2 `keep_n = min(round(n*1.6), n+4)` clips instead of `n`. Per-clip `_auto`
  no longer set here — moved to `attach_auto(clips, loud)`, called in `pipeline.analyze()` AFTER
  `_verify_and_refine` so the sfx offsets sit on the final bounds.
- Wired: `pipeline.analyze()` (`"Checking the flow of each moment"`, 85 %) and CLI `process_video()`.
- `_coherence_filter` + `CoherenceSet`/`CoherenceVerdict` deleted (grep-clean). Gated on the
  existing `cfg["coherence_check"]` flag — off ⇒ stage-1-only == today.
- Stage 1 (`_find_clips_groq`) `share` **unchanged** — the free tier is 8k TPM and over-generating
  per window caused multi-minute `retry-after` stalls; the fuller pool now comes from `keep_n`.

### Verification (still to run by the user, one real analysis)

**Goal:** after Stage-1 proposes candidates, ONE pass over all of them together confirms each is a
complete beat, tightens the bounds, and assigns a decomposed score. Borderline clips are
*refined*, not binary-dropped — this is what fixes "only 2 clips from a 10-min video".

### 1a. Extend the `Clip` model (clipfinder.py ~L391)
Copy the existing field + validator style. Add:
```python
    beat_type: str = "moment"          # reaction|bit|story|quote|fail|wholesome|moment
    hook_line: str = ""                # verbatim first line of the beat (for boundary snap)
    payoff_line: str = ""              # verbatim payoff/last line of the beat
    arc: dict = Field(default_factory=dict)   # {"hook":0-3,"arc":0-3,"quote":0-2,"standalone":0-2}
```
Reuse the `_stringify` validator for `beat_type/hook_line/payoff_line`. Keep `score` as-is; Stage 2
overwrites it from `arc` (below).

### 1b. New: `_verify_and_refine(clips, segments, cfg) -> list[Clip]`  (put next to `_coherence_filter`)
For each candidate build a context block = its transcript **plus ~15 s before and ~10 s after**
(slice `segments` on time; reuse `_clip_transcript` pattern ~L865 but widen the window). Number them.
One LLM call, both backends, following the exact call shapes in Phase 0:

- **Claude:** `messages.parse(output_format=VerifySet)` where
  `class Verdict(BaseModel): index:int; keep:bool=True; start_seconds:float|None=None;
  end_seconds:float|None=None; hook_line:str=""; payoff_line:str=""; beat_type:str="moment";
  hook:int=Field(ge=0,le=3); arc:int=Field(ge=0,le=3); quote:int=Field(ge=0,le=2);
  standalone:int=Field(ge=0,le=2); reason:str=""`
  `class VerifySet(BaseModel): verdicts:list[Verdict]`
- **Groq:** plain prompt ending with the JSON shape string + `_loads_lenient` + manual dict→field
  reads inside `try/except` (copy `_coherence_filter`'s Groq branch ~L892-900).

Prompt instruction (system for Claude / prefix for Groq) — frame it as *confirming the arc*:
```
Each item is a candidate short-form clip: its transcript, plus a few seconds of lead-in and
tail for context (the extra context is NOT part of the clip). For EACH index decide:
  - keep=false ONLY if: the words are garbled speech-to-text word-salad; OR there is no real
    payoff inside the clip (it ends on setup); OR it staples two unrelated moments together;
    OR it starts mid-thought with no way to add setup from the lead-in.
  - otherwise keep=true and TIGHTEN it: set start_seconds to the first line that a viewer needs
    (use the lead-in if the setup is there), end_seconds to just after the payoff / final
    reaction (never trailing dead talk). Copy hook_line and payoff_line verbatim from the transcript.
  - beat_type: reaction (loud outburst) | fail (something goes wrong) | bit (a joke/routine) |
    story (a told anecdote with a punchline) | quote (one strong line) | wholesome | moment.
  - score the clip 0-N on each: hook (instant attention, 0-3), arc (complete setup→payoff, 0-3),
    quote (a line worth repeating, 0-2), standalone (zero outside context needed, 0-2).
Drop nothing for being "merely fine" — that is what the score is for.
```
Apply results: `keep=false` OR `arc < 2` → drop. Else overwrite
`c.start_seconds/end_seconds` when the model returned refined values *and* they still enclose a
real segment; set `c.beat_type/hook_line/payoff_line`; set `c.arc = {...}` and
`c.score = hook + arc + quote + standalone` (0-10 already). On call failure: `except` → return
`clips` unchanged (today's fallback). Never return `[]` — `return kept or clips`.

### 1c. Boundary snap from `hook_line` / `payoff_line`  (feed into `_finalize`)
In `_snap_clip` (or a small helper called just before it in `_finalize` ~L638), if `c.hook_line`
matches a segment's `text` (normalised, `in` either direction), set `c.start_seconds = that
segment["start"]` before the existing pause/sentence snap runs; same for `payoff_line` → segment
`end`. Fall through to the current mechanical snap when there's no match. This is strictly additive.

### 1d. Wire it in
- `pipeline.analyze()` (~L67): replace `cf._coherence_filter(...)` call with `cf._verify_and_refine(...)`,
  keep the `on_progress(85, "Checking the flow of each moment")` line (update the message).
- CLI `process_video()` (~L972-976): same swap; keep the `coherence_check` gate + the
  "dropped N garbled clip(s)" print (now "tightened / dropped").
- Keep the old `_coherence_filter` + `CoherenceSet` in the file (dead-safe) OR delete both — grep
  first: `grep -n "_coherence_filter\|CoherenceSet\|CoherenceVerdict" clipfinder.py pipeline.py`.
- Gate on the existing `cfg["coherence_check"]` flag (rename nothing) — off ⇒ Stage 1 only, today's path.

### Verification
- `python -c "import ast; ast.parse(open('clipfinder.py').read())"` clean; `Clip(**{"start_seconds":0,"end_seconds":1})` still builds.
- Real run: `curl -s localhost:8000/api/add -d '{"path":"<a 8-15 min video>"}'`, wait for done, then
  `python -c "import json;d=json.load(open('web_data/<job>/analysis.json'));print(len(d['clips']));[print(c['start_seconds'],c['end_seconds'],c.get('beat_type'),c.get('arc'),c['score']) for c in d['clips']]"`.
  Expect: **≥ 4 clips** for a 10-min video (backfill + no binary drops), every clip has `arc` with
  4 keys summing to `score`, `beat_type` set, bounds tightened vs the raw Stage-1 seconds.
- `grep -n "_verify_and_refine" clipfinder.py pipeline.py` → defined once, called from both analyze + CLI.
- `grep -n "response_format.*json_schema" clipfinder.py` → **no match** (Groq path stays prompt-shaped).
- CLI still runs: `python clipfinder.py "<video>" --dry-run` prints clips, no network beyond the LLM calls it already makes.

---

## Phase 2 — Running global context + chain-of-thought  (PODTILE + CoT)

**Goal:** each Stage-1 window knows the whole-video vibe and what's already been picked, so it stops
re-picking the same bit and stops missing setup that started in the previous window.

### 2a. One cheap whole-transcript summary  (`_video_gist(segments, cfg) -> str`)
Take the first ~400 and last ~400 words of `as_timeline(segments)` (or every Nth segment to ~1500
chars) → one LLM call, `temperature=0`, ask for **"2 sentences: what is this video, what's the
energy/vibe"**. Groq: plain prompt, take `.choices[0].message.content.strip()`. Claude: plain
`messages.create` (no schema needed), take `.content[0].text`. On failure → `""`. Compute once in
`_find_clips_groq` / `_find_clips_claude` before the window loop; pass into the prompt builder.

### 2b. Running "already picked" list
In `_find_clips_groq`'s window loop (~L714): after each window's `_nonoverlap`, append one-liners
`f"- [{mm:02d}:{ss:02d}] {c.hook or c.quote or c.title}"` to a `picked_so_far` list. Pass the
current list into `_groq_pick_window` → into the prompt:
```
VIDEO: {gist}
ALREADY SELECTED (do NOT pick another clip about the same bit):
{picked_so_far or "(none yet)"}
```
For `_find_clips_claude` (single call, whole transcript) just prepend `VIDEO: {gist}` — it already
sees everything, so no running list needed.

### 2c. Chain-of-thought before the JSON  (in `_groq_pick_window` ~L659 and the Claude user msg)
Change the instruction so the model must **first** write a short prose list, then the JSON:
```
STEP 1 — in plain text, list the 6-10 strongest standalone moments in this section. For each:
one line = timestamp range + why it lands (the hook, the payoff, the reaction).
STEP 2 — then output ONLY the JSON object described above, drawn from your Step-1 list.
```
`_loads_lenient` already skips prose and grabs the trailing `{...}` — verify with the existing
`re.search(r'\{[^{]*"clips".*\}', ...)` branch (~L639). Bump `_groq_pick_window`'s `max_tokens`
path if `finish_reason == "length"` shows up (it already retries on that ~L694).

### Verification
- Diff two real runs of the same video with `coherence_check` on: Phase 2 run should have **fewer
  near-duplicate clips** (no two clips whose `hook`/`quote` describe the same joke) and clips whose
  `start_seconds` sit earlier (setup pulled in from context).
- `grep -n "_video_gist\|picked_so_far\|ALREADY SELECTED" clipfinder.py` → present.
- Token check: print `usage` (Claude) / count Groq calls — Phase 2 adds **exactly one** extra call
  (`_video_gist`) per analysis, not one per window.
- `--dry-run` still works; `_video_gist` failure (no key / rate limit) → run continues with `gist=""`.

---

## Phase 3 — Reaction track from Whisper  (reclaim the dropped signal)

**Goal:** the segments `_segment_action` throws away (held screams, "ARGH ARGH", repetitive
garble) are exactly where the streamer/room reacts. Keep their timestamps, cross them with the
loudness envelope, and hand the top reaction moments to Stage 1 as seeds.

### 3a. `transcribe()` — keep drop-timestamps  (clipfinder.py ~L348-361)
Where `act.startswith("drop")` currently `continue`s, first append
`reactions.append({"start": start, "end": end, "kind": act.split(":",1)[1]})` for the
"reaction-ish" kinds only: `repetitive`, `single-word-repeat`, `held-noise`, `low-confidence`
(NOT `empty` / `no-speech`). For `compress:held-line` also append (kind `"held-line"`).
`transcribe()` currently returns `list[dict]` — change to also return reactions. Least-invasive:
attach as a module-scoped side-channel is ugly; instead return `(segments, reactions)` and update
the **two** callers: `pipeline.analyze()` (~L59) and CLI `process_video()` (~L963). Default the
tuple-unpack so a caller that ignores it still works: `segments, reactions = transcribe(...)`.

### 3b. `reaction_track(reactions, loud, dur) -> list[dict]`  (new, near `loudness_track`)
Merge overlapping/adjacent reaction spans (< 2 s apart). For each merged span, score it:
`loud_here = max(loud[int(a):int(b)+1] or [-90])`; keep spans where `loud_here > p75` (from
`_loud_stats`). Return `[{"start","end","loud"}]` sorted by `loud` desc, top ~12.

### 3c. Seed Stage 1  (pass into `_find_clips_*` and the window prompt)
`pipeline.analyze()` computes `rx = cf.reaction_track(reactions, loud, duration)` and passes
`find_clips(segments, cfg, loud, reactions=rx)`. Thread `reactions=None` through
`find_clips → _find_clips_groq/_claude → _groq_pick_window`. In the prompt, for reaction
timestamps that fall inside the current window:
```
BIG REACTIONS were detected at: {"04:12, 07:48, ..."}
For each, check the transcript around it for a clippable beat — a loud reaction with a quotable
setup or follow-up line is prime. Ignore any that are just noise with no line.
```
### 3d. Feed it to `_finalize` energy too (optional, cheap)
In `_finalize`, a clip whose `[start,end]` contains a reaction-track timestamp gets a small extra
`+0.3` on `c.score` (capped). Keeps Phase 1's `arc` primary.

### Verification
- `python -c "from clipfinder import transcribe"` — signature is `(mp3, workdir, cfg)` → returns
  a 2-tuple; both callers updated (`grep -n "= transcribe(" pipeline.py clipfinder.py`).
- Real run on an IShowSpeed-style video: `reaction_track` returns ≥ 3 spans; print them; eyeball
  that they land on obvious hype seconds (cross-check against the `loud` array).
- At least one final clip's `start/end` should bracket a reaction timestamp.
- Video with calm audio (a talk) → `reaction_track` returns few/none, analysis unchanged. No crash on `reactions=[]`.

---

## Phase 4 — `beat_type` drives content-aware auto-edit  (F)

**Goal:** the sound + effects suggestion (`_auto_edit`, clipfinder.py ~L615) stops being only
loud-vs-quiet and uses the beat's *kind*.

### 4a. `_auto_edit(c, en, stats)` — take `beat_type` into account
`c` already carries `beat_type` after Phase 1. Branch the returned dict (keep the `hot` energy
flag as a secondary modifier):
| beat_type | crop | caption | color | sfx |
|---|---|---|---|---|
| `reaction` / `fail` | cropfill zoom 1.15, py 0.4 | bold + pop + karaoke | punchy | whoosh@0 + **impact** at `peak_at` |
| `bit` | cropfill zoom 1.08 | bold + bounce + karaoke | punchy (if hot) / none | whoosh@0 + **impact/ding** at `peak_at` |
| `story` | cropfill zoom 1.03, slow | clean + fade, no karaoke | film | whoosh@0 only, gain −8 |
| `quote` | cropfill zoom 1.06 | box + fade, no karaoke | none | **ding** at the quote line, no whoosh |
| `wholesome` | cropfill zoom 1.05 | clean + fade | warm | soft whoosh@0 gain −9 |
| `moment` (fallback) | today's hot/calm logic | — | — | — |
Keep everything else (`vertical: True`, `aspect: "16/9"`, `blur`) as-is.

### 4b. Surface it (tiny)  — `web/app.js`
`maybeApplyAuto(c)` already consumes `c.auto` generically → **no change needed** for behaviour.
Optional: in `reflectAutoBadge()`, when auto is on, show the beat: `b.textContent = "✨ " +
(c.auto.beat_type || "AI edit")`. `analysis.json` clip already has `beat_type` at top level too
(from `c.model_dump()` once Phase 1 adds the field) — the badge can read `c.beat_type`.

### 4c. Review card hint (optional)  — `renderReview()` in app.js (~L378)
Add a small pill on each `.rv-card` showing `c.beat_type` next to the score, so the user sees the
AI's read before opening the editor. Pure display.

### Verification
- Real run → `analysis.json`: clips with `beat_type: "reaction"` have `caption.style == "bold"` &
  an `impact.m4a` sfx entry; a `beat_type: "story"` clip has `caption.style == "clean"`, `color.preset == "film"`,
  and only a whoosh. A no-`beat_type` clip falls back to the Phase-0 hot/calm output (regression-safe).
- `grep -n "beat_type" clipfinder.py web/app.js` → used in `_auto_edit` + (optionally) the badge.
- Browser: open a job, badge shows `✨ reaction` / `✨ story`; strip + re-apply still works (Phase-F
  didn't touch `toggleClipAuto` / `applyAutoEdit`).

---

## Final Phase — Verification

1. `python -c "import ast;[ast.parse(open(f).read()) for f in ('clipfinder.py','pipeline.py')]"` +
   `node -e "new Function(require('fs').readFileSync('web/app.js','utf8'))"` — both clean.
2. **Full real analysis** on one 8-15 min high-energy video AND one calm talk:
   - high-energy: ≥ 6 clips, most `beat_type reaction/bit/fail`, bounds tightened, no near-dupes,
     auto-edits are punchy with impact SFX on the peak second.
   - calm: still produces clips (≥ 4), `beat_type story/quote`, calm auto-edits, no crash on
     empty reaction track / flat loudness.
3. **Degrade tests:** `coherence_check=false` in settings → Stage-1-only path == pre-plan behaviour.
   Kill network mid-analysis → `_video_gist` / `_verify_and_refine` `except` → analysis still finishes.
4. **CLI regression:** `python clipfinder.py "<video>" --n 6` — runs end to end, prints clips, cuts them.
5. `grep -rn "import numpy\|import librosa\|import torch\|whisper_timestamped\|response_format.*json_schema" clipfinder.py pipeline.py` → **no matches** (no new deps, Groq stays prompt-shaped).
6. Token/cost sanity: one analysis adds **≤ 2** extra LLM calls total vs pre-plan (`_video_gist` +
   the verify pass replaces the old coherence call, so net ≈ +1). Print/observe on a Groq run.
