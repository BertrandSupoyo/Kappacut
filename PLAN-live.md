# PLAN-live.md — Kappacut: near-live clipping from a stream

Fourth roadmap, after `PLAN-multiuser.md`, `PLAN-boost.md` and `PLAN-v2.md`. Written
2026-10-25, the day the dev stack moved to a paid Groq key and the picker became a 20b/120b
mix. Goal: a streamer points OBS at Kappacut, streams as usual, and clip candidates appear in
their project **3–5 minutes after the moment happens**, editable and renderable with the
editor that exists today.

Phases are written to be executed in order and each is independently shippable — Phase A
alone already gives "clips a few minutes after the stream ends" with no engine changes.
Append deviations and verification results to each phase as it completes, the same way the
earlier plans log them.

---

## Locked constraints (do not violate)

**1. The stream comes from the streamer, never from the platform.** Ingest is a second
output the streamer sends us (RTMP/SRT from OBS). We never pull a Twitch/YouTube/Kick stream
or download a VOD — neither platform offers that in its official API, and the tools that do
it break their terms. This also keeps consent unambiguous: only the owner of the stream key
can send us video.

**2. No local ML.** Carried forward unchanged from `PLAN-v2.md`. Transcription stays on Groq
Whisper, picking and judging stay on Groq gpt-oss. The ingest server and ffmpeg are signal
plumbing, not ML.

**3. The engine is reused, not forked.** Live analysis calls the same `kappacut.py`
functions the upload path uses (`transcribe`, `loudness_track`, `_windows`,
`_groq_pick_window`, `_finalize`, `_verify_and_refine`, `attach_auto`, `face_centres`,
`pipeline.render_one`). Anything live needs that they don't do becomes a small new function
next to them, with tests, never a copy.

**4. No arq job runs for the length of a stream.** `job_timeout_seconds` is 3600 and a
4-hour job would hold a worker slot for its whole life. A stream is a row in the database;
the work it causes is a series of short, bounded tasks.

---

## Considered and not taken

- **Pulling the stream or the VOD from Twitch/YouTube** — terms of service (constraint 1).
- **Instant clips (< 30 s latency)** — needs a streaming speech-to-text vendor; Groq Whisper
  is a batch endpoint. A second vendor, a second bill and a second failure mode for a latency
  most streamers won't notice. Loudness and chat markers (below) cover "something just
  happened" within seconds without it.
- **Self-hosted Whisper for live** — violates constraint 2.
- **Auto-posting clips to TikTok / YouTube Shorts** — official upload APIs exist; still
  deferred as product scope (carried from `PLAN-v2.md`). Separate plan.
- **YouTube live chat as a signal** — the Data API's quota cost per poll makes it expensive
  to read continuously. Twitch chat only in this plan (Phase D).

---

## Phase 0 — Discovery and decisions (do before any code)

### Verify (record what you find in this section)

- [ ] **Ingest server: MediaMTX** (`bluenviron/mediamtx`, single binary, official Docker
      image). Confirm against its current README: RTMP + SRT ingest; recording to segments
      with a configurable segment length (target **10 s**); a hook that runs when a segment
      is complete (`runOnRecordSegmentComplete` or the current equivalent); HTTP-based
      publish authentication (`authHTTPAddress` or the current equivalent) so the web app
      can accept or reject a stream key. Pin an exact image tag.
- [ ] **OBS second output.** OBS's built-in Stream settings send to one destination.
      Confirm the current options for a second one (the `obs-multi-rtmp` plugin, a newer
      native feature, or a relay) and write the streamer-facing setup steps from what
      actually works.
- [ ] **Groq Whisper on short chunks.** Billing minimum per request (sources say 10 s),
      behaviour on a 2-minute chunk that starts or ends mid-word, and whether
      `word_timestamps` survives the chunk path in `transcribe()`.
- [ ] **Twitch EventSub** (Phase D only): `stream.online` / `stream.offline` and the chat
      message subscription — required scopes, WebSocket vs webhook transport, rate limits.
- [ ] **Bitrate reality.** Ask two or three target streamers what they send (typically
      6–8 Mbps 1080p). That number sizes disk and bandwidth below.

### Decisions the owner must make

1. **Who gets live minutes.** Constraint 1 of `PLAN-v2.md` ("free product, paid calls stay
   dark") was written for a free Groq key. A 4-hour stream is ~$0.22 of Groq calls (cost
   model below). Proposed default: live is **off for everyone** (`quota_live_minutes_per_month
   = 0`) and an admin grants minutes per user from the Admin page.
2. **How long raw recordings are kept.** Proposed: 7 days for the raw stream, rendered clips
   follow normal retention.
3. **Maximum concurrent streams** for the box (proposed: 5 to start; see capacity).

---

## Cost and capacity model (measured 2026-10-25, paid Groq key, Ryzen 5 5600G)

| Item | Per stream-hour | Source |
|---|---|---|
| Whisper large-v3-turbo | $0.040 | Groq list price, $0.04 / audio hour |
| Picker (20b/120b mix) + judge (120b, low effort) | ~$0.015 | measured: 19.4-min video, pick $0.0042 + judge ~$0.002 |
| Live overhead (smaller windows, repeated judging) | ~$0.010 | estimate — measure in Phase B |
| **Groq total** | **~$0.065** | a 4-hour stream ≈ **$0.26** |
| Recording storage at 6 Mbps | 2.7 GB | → ~$0.01 per stream-hour at 7-day retention on R2 |
| Ingest bandwidth | 6–8 Mbps per live stream | 5 concurrent streams ≈ 40 Mbps sustained |

- **CPU:** recording is a stream copy (near zero). Audio extraction + loudness measured at
  ~0.5 CPU-s per minute of video. Renders are unchanged (1.67 CPU-s per second of a vertical
  captioned clip) and only happen when someone renders. Live adds almost no CPU; bandwidth
  and disk are what grow.
- **Groq limits:** one stream uses roughly 1–1.5k tokens a minute. The paid key allows
  250k tokens/min per model — not a constraint at any stream count this box can ingest.
- **Disk during recording:** segments land on local disk before upload. Size the volume for
  `max concurrent streams × 10 min of video` of buffer (≈ 3.5 GB at 5 streams), not whole
  streams — segments are uploaded and deleted as they close.

---

## Phase A — Ingest and recording (no AI changes)

**Depends on:** Phase 0.

**Goal:** a user gets a stream key, streams from OBS, and the recording lands in their
project in the bucket. When the stream ends, the existing analyze job runs on the full
recording. Shippable on its own: clips a few minutes after the stream ends.

### What to implement

1. **`mediamtx` service** in `compose.yaml` (pinned tag), RTMP on 1935 published directly —
   RTMP is not HTTP, so it does not go through Caddy. Records every path to a new `live`
   volume in 10-second segments. Its auth and segment hooks call the web app over the
   compose network.
2. **Stream keys.** New `live_keys` table (`user_id`, `key_hash`, `created_at`,
   `last_used_at`). A key is 32 random bytes shown once on the Go Live page; only a SHA-256
   hash is stored. "Regenerate" replaces it.
3. **`live_sessions` table** (`id`, `user_id`, `project_id`, `status`:
   `waiting|live|ended|failed`, `started_at`, `ended_at`, `last_segment_at`,
   `seconds_recorded`, `segments`). A session owns one project; `projects.status` gains
   `live`. One Alembic migration.
4. **Hook routes** in a new `app/live.py`, following `app/uploads.py`'s model exactly:
   - `POST /api/live/auth` — MediaMTX asks before accepting a publish. Look the key up by
     hash, check `quota_live_minutes_per_month`, the per-user single-stream rule and
     `live_max_sessions`, then create the session and project.
   - `POST /api/live/hooks/segment` — a segment closed. **Trust nothing in the payload**
     (same rule as the tusd hook, see `dacbbb1`): the path must map to an active session,
     and the file must sit under that session's directory in the `live` volume. Enqueue
     `live_segment_task(session_id, path)`.
   - `POST /api/live/hooks/end` (publish stopped) — mark the session `ended` after a grace
     period (reconnects within 60 s continue the same session).
   - The Caddyfile 404s `/api/live/*/hooks` and `/api/live/auth` from outside, like
     `/api/upload/hooks`.
5. **`live_segment_task`** (arq, seconds long): upload the segment to
   `live/<user>/<project>/seg_<n>.ts` via `app/storage.py`, delete the local file, bump
   `seconds_recorded`. Storage keys are built only in `app/storage.py`.
6. **End of stream:** a task concatenates the segments (ffmpeg concat demuxer, stream copy)
   into the project's normal source key and enqueues the **existing** analyze job. The
   editor then works as it does for an upload.
7. **Go Live page** in the SPA (`/app/live`): the server URL and stream key (copy buttons),
   OBS setup steps from Phase 0, and live status (waiting / live for 01:23:45 / ended). Use
   the existing SSE pattern (`analyze_events`) for status.
8. **Quota + limits** in `app/config.py`: `quota_live_minutes_per_month` (default 0),
   `live_max_sessions` (default 5), `live_max_hours_per_session` (default 6, the session is
   stopped past it), `live_retention_days` (default 7).

### Verification checklist

- [ ] OBS (with the Phase 0 second-output method) streams for 30 min; every segment arrives
      in the bucket and the local `live` volume stays under one minute of buffer.
- [ ] Pull the network cable for 20 s mid-stream: the session continues, the gap is visible
      as a gap (timestamps are not compressed).
- [ ] A wrong or regenerated key is rejected at publish; a user with 0 live minutes is
      rejected with a readable reason on the Go Live page.
- [ ] A forged `POST /api/live/hooks/segment` from outside returns 404; one from inside with
      a path outside the session directory is refused.
- [ ] After the stream ends, the normal analysis runs and the editor opens on the recording
      with clips.
- [ ] `live_max_hours_per_session` stops a session cleanly and the recording is still
      analysed.

### Anti-pattern guards

- **Do not** run the recording, or a whole session, inside an arq job (constraint 4).
- **Do not** store or log a stream key in plain text; only its hash.
- **Do not** read a segment that is still being written — act only on the segment-complete
  hook.
- **Do not** mount the `live` volume into the web container; only MediaMTX and the worker
  touch it.

---

## Phase B — Near-live analysis

**Depends on:** Phase A. (The broken-judge-reply fix this phase needed landed 2026-10-25 —
see "Known-good state". Live runs the judge many times per stream, so re-check its broken
reply rate in the latency sweep below.)

**Goal:** clip candidates appear in the project while the stream is still running.

### What to implement

1. **Transcribe in 2-minute chunks.** Every 12th segment, `live_chunk_task` downloads the
   last 12 segments plus 5 s of overlap, extracts audio (`extract_audio`) and calls
   `transcribe()`. Shift timestamps to stream time. **Seam rule:** keep the previous chunk's
   lines up to the frontier and the new chunk's lines from the frontier on, so a line cut at
   the seam is transcribed once, whole.
2. **Store per-chunk transcripts** in a `live_chunks` table (`session_id`, `idx`,
   `start_s`, `end_s`, `segments` JSONB). `GET /api/projects/:id` concatenates them while
   the project is live; the final pass (Phase C) writes `analyses.segments` once.
3. **Loudness per segment.** `loudness_track()` on each 10-s segment's audio as it lands,
   appended to a per-session loudness array. A sharp rise against the session's running
   median raises a **hot moment marker** (stream time, level) within ~15 s — shown on the
   live page before any transcript exists.
4. **Incremental windows.** New `_live_windows(segments, frontier, span)` next to
   `_windows()`: returns only windows that end before the transcript frontier and were not
   returned before. Start with `span = PICKER_WINDOW_SEC` (240 s); try 120 s in the
   verification sweep below — shorter windows cut latency at the cost of more picker calls.
5. **Pick each closed window** with `_groq_pick_window` using the existing model pool
   (`groq_pick_models`, rotation and daily-cap fallback unchanged). Run `_finalize` only on
   candidates whose `end + tail_seconds` sits behind the frontier, so snapping never looks
   past the transcript.
6. **Judge in batches.** When 8 new finalized candidates exist or 10 minutes pass, run
   `_verify_and_refine` on the batch. Judged clips are written to `analyses.clips` marked
   `provisional: true` with `virality` and `breakdown`, so the existing clip list shows
   them.
7. **Events.** A `live/events` SSE route (same shape as `analyze_events`) sends
   `{status, seconds_recorded, transcript_frontier, markers, clips_found}` so the live page
   and editor update without polling.
8. **Spend guard per session.** Track Groq usage per session (prompt + completion tokens ×
   list price, as `model_test2.py` did) and stop analysis — not recording — past a cap
   (`live_max_usd_per_session`, default $1.00). The recording still gets the end-of-stream
   analysis.

### Verification checklist

- [ ] **Latency, measured:** for 20 marked moments across two streams, time from the moment
      to its clip appearing. Target p50 ≤ 3 min, p95 ≤ 5 min. Record both window spans
      (240 s / 120 s) and pick one.
- [ ] **Seams:** no transcript line duplicated or lost across 30 chunk seams (diff against a
      one-shot transcription of the same recording).
- [ ] **Same engine, same picks:** live candidates vs the end-of-stream analysis of the same
      recording overlap ≥ 70% (same "50% time overlap" rule as `model_test2.py`).
- [ ] **Cost, measured:** Groq spend per stream-hour from the session ledger; update the
      cost table above.
- [ ] A Groq daily-cap or outage mid-stream degrades to "recording only" with a message on
      the live page; recording never stops because analysis failed.
- [ ] Hot-moment markers fire within 20 s of a loud spike in a test stream.

### Anti-pattern guards

- **Do not** re-transcribe or re-pick anything behind the frontier. Every chunk and window
  is processed exactly once.
- **Do not** finalize a clip whose padded end extends past the transcript frontier.
- **Do not** let analysis tasks for one stream starve uploads: live tasks go to the same
  queue but each is seconds long, and at most one `live_chunk_task` per session is in
  flight.
- **Do not** let a failed analysis task touch recording state.

---

## Phase C — Edit and render while live, and the final pass

**Depends on:** Phase B.

### What to implement

1. **Render from segments.** For a clip range, download the segments covering
   `[start − 2 s, end + 2 s]`, concat them (stream copy) into a temp excerpt and call
   `pipeline.render_one` with times shifted to the excerpt. Same per-clip task as today's
   render, same quotas.
2. **Faces on demand.** Run `face_centres` on the same excerpt when a provisional clip is
   opened in the editor or rendered, not for every candidate.
3. **Final pass at stream end** replaces Phase A's "concat and re-analyse from scratch":
   concat the recording into the source key, write `analyses.segments` from `live_chunks`,
   run `detect_category` on the full transcript, re-judge the top candidates in one batch,
   `attach_auto`, faces for the final list, and clear `provisional`. No second Whisper pass:
   the transcript already exists.
4. **Edits survive the final pass.** Provisional clips get a stable `clip_id` when created,
   and saved edits are keyed by it. This is the "Clip IDs" item `PLAN-v2.md` deferred; live
   makes it unavoidable, because the final pass re-ranks the list while the user may already
   be editing.

### Verification checklist

- [ ] Render a provisional clip while the stream is still running; output matches a render
      of the same range after the stream ends.
- [ ] Edit a provisional clip, let the stream end; the edit is still attached to the same
      moment after the final pass.
- [ ] The final pass makes **no** Whisper call (check the session's Groq ledger).

### Anti-pattern guards

- **Do not** re-transcribe the full recording at the end.
- **Do not** key edits by list index for live projects.

---

## Phase D — Twitch chat as a signal (optional)

**Depends on:** Phase B.

### What to implement

1. **"Connect Twitch"** on the Account page (OAuth, scopes from Phase 0). Store tokens
   encrypted; disconnect deletes them.
2. **EventSub `stream.online`** can open the session automatically (pre-arm "waiting").
3. **Chat rate as a signal.** Messages per 10 s against the session's running median; a
   spike becomes a marker immediately (no chunk wait) and feeds the reaction finder
   (`_reaction_candidates`) as a third source next to loudness. Message text is not stored —
   only counts per second.

### Verification checklist

- [ ] Chat spikes in a test stream produce markers within 10 s.
- [ ] With chat on vs off, the live-vs-final overlap and the user's keep rate (`eval-picks`)
      are recorded; keep the signal only if it helps.

---

## Phase E — Operations

- **Retention:** `retention_gc` deletes raw `live/` segments after `live_retention_days`;
  the concatenated source follows normal project retention.
- **Health:** `/api/health` adds `live_sessions` and the `live` volume's free space; uploads
  and new live sessions are refused below `disk_min_free_pct`, like uploads today.
- **Admin page:** live sessions (user, duration, spend, status) with a Stop button; live
  minutes per user next to the existing quota columns.
- **DEPLOY.md:** port 1935 (and SRT if used) in the firewall, bandwidth sizing from the cost
  table, the `live` volume, and the OBS setup steps.

---

## Final phase — Verification

1. Full CI matrix (`ruff`, `alembic upgrade head`, autogenerate drift check, import smoke
   test, `npm run build`, `tests/`).
2. A 2-hour real stream end to end: markers, provisional clips, a render while live, final
   pass, edits kept. Record latency p50/p95, Groq spend, peak disk buffer.
3. Update the cost table and `DEPLOY.md` capacity notes with the measured numbers.

---

## Known-good state at the start of this plan (2026-10-25)

- **Picker is a 20b/120b mix** (`groq_pick_models` in `kappacut.toml`): windows alternate
  models; a model whose daily cap is spent is dropped for the rest of the video. Judge is
  `gpt-oss-120b` at `groq_verify_effort = "low"`. Tested live: medium effort costs ~3× and
  broke more often, so low stays. These changes are **not yet committed**.
- **Paid Groq key** in dev: 250k tokens/min and 500k requests/day per model. Measured: an
  8-minute video analyses in 22.8 s (was ~2.9 min on the free key).
- **Fixed 2026-10-25 — broken judge replies:** the judge sometimes returns a reply the code
  flags as broken even after its retry (1 of 6 low-effort runs on a 19-minute video). The
  code used to keep that reply's scores, which pushed most clips under the quality floor
  (seen: 6 clips → 1). It now keeps the stage-1 ranking instead, as the "no usable
  verdicts" path does (regression test in `tests/test_clip_brain.py`).
- **Rate limiter on the paid key:** `GROQ_TOKENS_PER_MINUTE=200000` is set in this dev
  machine's `.env`. Any new deploy on a paid key needs the same, or live sessions will queue
  behind the 8,000 free-tier default.
- **Dev stack:** `.env` carries the `S3_*` block (s3mock on 9090; `.env.example` fixed to
  match) and `COMPOSE_PROJECT_NAME=clipfinder`, which keeps this machine's pre-rename
  volumes.
