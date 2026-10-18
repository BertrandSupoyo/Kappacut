# PLAN-v2.md — clipfinder: object storage + scene-aware cuts

Third roadmap, after `PLAN-multiuser.md` (local → multi-user, 8 phases, all shipped) and
`PLAN-boost.md` (taste plumbing → AI thumbnails, phases A–F, all shipped). Two phases of
substance here, both chosen deliberately over a longer list — see "Considered and not
taken" below for what was left out and why. Each phase is independently shippable.

Phases are written to be executed in order. Append deviations and verification results to
each phase as it completes, the same way `PLAN-boost.md` logs them.

---

## Locked constraints (do not violate)

**1. clipfinder stays free, open-signup.** Reaffirmed 2026-09-24 against a full cost model
(below). One shared server-side Groq key, protected by `app/quota.py`, `app/ratelimit.py`
and `app/groq_gate.py`. No billing, no paid tiers, no per-user keys. The consequence, which
this plan accepts rather than works around: **anything with a real per-call cost stays
dark.** The Claude picker / verify / vision machinery remains in the codebase and dormant
(`PLAN-multiuser.md:57`), and Phase F's AI thumbnails stay off, because the credit ledger
can gate spend but nothing in a free product can fund it.

**2. No local ML.** Carried forward from the multi-user migration, which deliberately
closed this off. No model weights, no GPU, no inference runtime in the deploy. Signal
processing in ffmpeg is *not* ML and is fair game — that is precisely what makes Phase B
possible.

**3. NEW — media no longer lives on local disk.** This unlocks `PLAN-multiuser.md:53`
("Local disk for media"), which was correct for a single-VPS v1 and has now expired. It is
not a spending decision: R2 is **cheaper** than the path it replaces (numbers in Phase 0),
so it is consistent with constraint 1. Everything else about the deploy — single VPS,
Docker Compose, Caddy, Postgres, Redis, one worker — is unchanged.

---

## Considered and not taken

Carried forward from `PLAN-boost.md:15` and still rejected: live TikTok/Instagram
trend-data scraping (ToS/reliability); voice cloning or AI avatars (likeness/consent);
AI B-roll or generated cutaways; multi-language auto-dub (separate future plan); any LLM
fine-tuning or local-model training pipeline (violates constraint 2).

Added this session:

- **Billing / paid tiers** — the one change that would unblock the most (dormant Claude
  picker at +$0.14/video, live thumbnails, per-user keys). Declined 2026-09-24;
  constraint 1 reaffirmed. Every item below it in this list is blocked *by that choice*,
  not on merit.
- **Vision-derived crop box for 9:16 reframing** — extend `vision_check` to return a
  subject box and feed it into the existing `cropfill` mode. Technically the best quality
  upgrade available and it respects constraint 2 (an API call, not a local detector). Blocked
  by constraint 1: it is a paid per-image vision call on every clip. Revisit only if billing
  is ever unlocked.
- **Direct publish to TikTok/YouTube/Shorts** — not the rejected scraping; these are
  official publish APIs. Deferred as product scope, not blocked.
- **Clip IDs + re-analyze with a new taste** — `ProjectEdit.state` is keyed by clip array
  index, so re-analyzing silently reattaches saved edits to different clips. Real bug,
  deferred this round. Anyone touching `Analysis.clips` ordering must fix this first.
- **GPU encoding (nvenc/QSV)** — 5–20× faster, needs a GPU host. No revenue and CPU is not
  the binding constraint (capacity math in Phase 0). Not now.
- **Editor proxy transcode + frame sprite** — was justified mainly by egress cost, and R2's
  $0 egress removes that justification. What remains is scrub latency and getting ffmpeg out
  of the web container. Do it only if the editor measurably feels slow after Phase A.

---

## Phase 0 — Documentation discovery (verified 2026-09-24)

All line numbers below were read from the current files **after** the review-fix commit
(`dacbbb1`) and are correct as of this writing. Numbers in `PLAN-multiuser.md` and
`PLAN-boost.md` predate that commit and have shifted — do not trust them. Re-verify
anything here if more than a few days have passed.

### tusd has a native S3 backend — this reshapes Phase A

`tusd/v2/pkg/s3store` speaks S3 multipart directly, and works against any S3-compatible
endpoint. CLI flags: `-s3-bucket`, `-s3-endpoint`, `-s3-part-size`, `-s3-min-part-size`,
`-s3-log-api-calls` (useful while bringing this up). Tuning knobs that matter:
`PreferredPartSize` must sit between `MinPartSize` and `MaxPartSize`, and
`MaxBufferedParts` controls how many parts tusd accepts from the client while another part
is in flight to S3 — raising it trades memory for throughput.

**Consequence: the entire staging dance disappears.** tusd writes the object itself, so
`post-finish` no longer receives a local path to move. That deletes, rather than modifies:

- `os.replace(staged, dest)` — `app/uploads.py:190`
- `_staged_path()` — `app/uploads.py:84`, the traversal guard added in `dacbbb1`
- `_discard()` / the `.info` sidecar cleanup
- `_sweep_stale_uploads()` — `app/retention.py:24`, called at `:46`. Replaced by an S3
  lifecycle rule on the upload prefix (tusd has no expiry flag; a bucket rule does).
- The `MEDIA/_uploads` staging dir and its `chmod 0o777` in `app/main.py`'s lifespan

What replaces the guard: `post-finish` receives an object key, and must verify it carries
the expected prefix before adopting it. **The threat model does not change** — the hook is
still only reachable from inside the compose network (Caddy 404s it) and still must not
trust its payload. The quota and concurrency re-checks added in `dacbbb1` stay exactly as
they are; only the path validation changes shape.

Docs: <https://tus.github.io/tusd/storage-backends/aws-s3/> ·
<https://pkg.go.dev/github.com/tus/tusd/v2/pkg/s3store>

### R2 pricing, and why this is cheaper than what it replaces

- Storage **$0.015/GB/month**; **egress $0.00**, all storage classes
- Class A ops (writes, lists) **$4.50/M**; Class B ops (reads) **$0.36/M**
- Free tier: 10 GB-month, 1M Class A, 10M Class B
- S3-compatible API

Against the current path (Hetzner volumes at **€0.0572/GB/month** + €1/TB egress beyond the
plan's included traffic), at 1,000 users × 1 GB:

| | today (Hetzner volume) | after (R2) |
|---|---|---|
| 1 TB stored | €57 / mo | $15 / mo |
| Egress | €1/TB past the included 20–30 TB | $0, uncapped |
| Durability | **none — no media backup exists** | 11-nines, replicated |
| Disk ceiling | 160 GB included, then volumes | none |

Roughly **4× cheaper on storage**, removes the egress ceiling entirely, and fixes the fact
that losing one volume today permanently loses every user's video. Ops cost is noise: a
video is on the order of 50 Class A ops (one multipart upload, ~6 clip writes, waveform,
frames), so 1,000 videos/month ≈ $0.23.

### ffmpeg scene detection — the recipe and its real cost

Verified forms, cheapest to parse last:

```
ffmpeg -i in.mp4 -filter:v "select='gt(scene,0.4)',showinfo" -f null - 2>scenes.log   # grep pts_time
ffprobe -show_frames -of compact=p=0 -f lavfi "movie=in.mp4,select=gt(scene\,.4)"     # machine-readable
ffmpeg -i in.mp4 -vf "select='gt(scene,0.4)',metadata=print:file=-" -an -f null -     # machine-readable
```

Thresholds: **0.1–0.3** catches soft transitions and fast camera movement; **0.4–0.5** is
the standard range for hard cuts; **0.6–0.7** only fires on massive changes. Start at 0.4,
expect to tune toward 0.3 for talking-head and streamer footage where cuts are softer.

**The cost, which must not be glossed over:** analyze currently never decodes video.
`extract_audio` passes `-vn` and `loudness_track` works on the extracted mp3, so
`clipfinder.py` touches video frames only in `grab_frame` and `cut_clip`. Scene detection
adds a **new full-video decode pass** — order of 1–3 CPU-min for a 60-min 1080p source,
against a current analyze cost of ~2–3 CPU-min total. That is a material increase to
per-video CPU and it must be measured, not assumed. Mitigations to try in this order:

1. Downscale before `select` — `-vf "scale=240:-2,select='gt(scene,0.4)'"`. The scene score
   is a whole-frame difference metric; it survives aggressive downscaling.
2. Sample rather than decode every frame — `-r 5`, or `-skip_frame nokey` for a keyframe-only
   pass (much faster, coarser; may be enough since we only need edges within `snap_max_shift`).
3. Fold it into an existing pass if one can be made to serve both purposes.

Sources: <https://ffmpeg-cookbook.com/en/articles/scene-detect/> ·
<https://www.ffmpeg-micro.com/blog/ffmpeg-scene-detection-auto-split-a-long-video-at-scene-changes>

### Code anchors each phase touches

**`app/storage.py` (101 lines) is the whole boundary** — the single place any media path is
constructed, and the reason Phase A is tractable at all. `MEDIA` :17, `_inside()` :20,
`project_dir` :27, `clips_dir` :34, `frames_dir` :40, `source_path` :46, `find_source` :51,
`waveform_path` :58, `thumbnails_dir` :62, `thumbnail_path` :68, `clip_path` :72,
`delete_project_media` :78, `builtin_sfx_dir` :84, `user_sfx_dir` :90, `sfx_path` :96.
Every one returns a local `Path` today.

**Media routes** (all `FileResponse`, all owner-checked): `app/projects.py` `media_source`
:450, `media_waveform` :463, `media_frame` :487, `media_thumbnail` :512, `media_clip` :526;
plus `app/sfx.py` `media_sfx` :101.

**Worker media use**: `app/jobs.py` `run_analyze` :134, `run_render` :225, `out_dir` :283,
`sfx_stage` :286, `_stageable_sfx` :60, `_fail` :44. `RenderOutput.bytes` is read from
`.stat().st_size` inside `run_render`'s drain — becomes the upload response size.

**Snapping (Phase B)**: `clipfinder.py` `_boundaries()` :613 returns
`(starts, ends, pauses)`; `_snap()` :625 is a generic nearest-point helper, reusable
unchanged; `_snap_clip()` :630 applies it per edge; `loudness_track()` :563 is the model for
what a `scene_track()` should look like. `snap_max_shift` default 4.0s at :64.

**ffmpeg needs a seekable local file.** `cut_clip` takes `video: Path` and `pipeline.render`
takes `out_dir: Path`. ffmpeg can read an `https://` input, but `render` makes N passes over
the same source and `-ss` seeking over HTTP is slow and re-fetches. **Decision: the worker
downloads the source to a `tempfile.TemporaryDirectory()` once per job** and uploads outputs
at the end. Do not plumb presigned URLs into `cut_clip`.

### Capacity, for context

CPU is not the binding constraint and this plan does not change that: one CCX23 (4 dedicated
vCPU, €85.99/mo) has ~2,100 vCPU-hours/month available for ffmpeg after reserving a quarter
for web/Postgres/Redis, against ~5–6 CPU-min per video today. Phase B adds to that number —
measure it. Storage was the binding constraint and Phase A removes the ceiling.

---

## Phase A — Media to S3-compatible object storage

**Status: code-complete 2026-09-25 — not yet verified against a real bucket.** The
verification checklist below is the gate; every box is still unticked. Deviations from the
plan as written, and what they cost:

- **Client library: `boto3` + `run_in_threadpool`, not an async S3 client.** The plan left
  this open. Presigning signs locally (no I/O), the worker is already synchronous inside
  `asyncio.to_thread`, and the web process does real S3 I/O on only a handful of paths — so
  the sync client keeps `app/storage.py` a flat module of small functions instead of nested
  async context managers, which is what made the rest of the phase mechanical. Request
  handlers wrap I/O in `run_in_threadpool`, the same idiom already used for ffmpeg.
- **`_seg()` allows a leading underscore.** Caught by the new tests: the shared `sfx/_builtin/`
  prefix would otherwise be rejected by a first-character rule written to stop `.` and `..`.
  An underscore has no traversal meaning, and allowing it preserves the identical-layout
  property that makes `migrate-media` a straight copy.
- **MinIO added to `compose.override.yaml`.** Not in the plan. Without it, `compose up` in dev
  now requires real R2 credentials, which would have made the round-trip checklist below
  unrunnable locally. `.env.example`'s dev block points at it and the bucket is created on
  first boot.
- **`tests/` exists now** — `test_storage_keys.py`, 11 tests, wired into CI. This is the
  project's first test suite, and it closes the "Key-guard tests mirroring the `_inside()`
  cases" checklist item below. Deliberately pytest-compatible but pytest-free so it runs
  under plain `python` with no new dependency.
- **`media_frame`'s `t` is now clamped** to the project duration (or `MAX_FRAME_SECOND`).
  Listed as still-open at the bottom of this file; it became a one-line fix while that route
  was being rewritten, so it was taken here rather than left.
- **`assert_disk_ok` and `/api/health` now measure `tempfile.gettempdir()`**, not
  `media_root`. The disk that matters is the worker's scratch space, since that is the only
  local disk media touches any more.

**Depends on:** nothing. Do this first; Phase B is independent but smaller.

**Goal:** media lives in one bucket, survives the loss of the box, and stops being billed
per GB of local volume and per TB of egress. `app/storage.py` stays the only module that
knows how media is addressed.

### What to implement

1. **Settings** (`app/config.py`) — `s3_endpoint_url`, `s3_bucket`, `s3_access_key_id`,
   `s3_secret_access_key`, `s3_region` (R2 wants `auto`), `s3_public_base_url` (optional, for
   a custom domain in front of the bucket), `presign_ttl_seconds` (default 300). Add the S3
   credentials to `check_production_ready()`'s public-deploy checks — a public deploy with no
   bucket configured should fail at boot, not on first upload.

2. **Rewrite `app/storage.py` as a key builder, keeping its current shape.** Every
   `*_path()` becomes a `*_key()` returning a `str` key under
   `users/<user_id>/projects/<project_id>/…` — the same layout the volume uses today, so the
   migration is a straight copy. Keep the guard discipline: `_inside()` becomes `_key()`,
   which asserts no `..` segment, no leading `/`, and that the caller-supplied leaf is a bare
   filename (the check `clip_path` :72 and `sfx_path` :96 already make). Add `put_bytes`,
   `get_to_file`, `presign_get`, `delete_prefix`, `head`. `find_source` :51 becomes a
   `list_objects_v2` on the project prefix.

3. **tusd writes directly to the bucket.** Switch the compose service to the S3 store
   (`-s3-bucket`, `-s3-endpoint`, `-s3-part-size`), drop the `-upload-dir` and the `media`
   volume mount. In `post-finish`, replace `_staged_path()` + `os.replace` with: read the
   object key from the hook payload, assert the expected prefix, and `CopyObject` it to the
   project's source key (server-side; no bytes through the app). Keep the quota and
   concurrency re-checks from `dacbbb1` untouched.

4. **Worker**: `run_analyze` and `run_render` download the source into a
   `TemporaryDirectory` at the top, run unchanged, and upload every output before marking the
   job done. `RenderOutput.bytes` comes from the upload response. `sfx_stage` becomes a temp
   dir populated by downloads — `_stageable_sfx` :60 keeps its bare-filename rule verbatim,
   since it now guards a key prefix instead of a path.

5. **Media routes** return `RedirectResponse(presign_get(key), status_code=302)` after the
   existing ownership check, instead of `FileResponse`. The ownership check stays in the app;
   the bytes never do. Short TTL (5 min) so a leaked URL expires. `media_waveform` :463 and
   `media_frame` :487 keep their generate-on-miss behaviour: download source → run ffmpeg →
   upload → redirect.

6. **Retention** (`app/retention.py`) — `delete_project_media` becomes `delete_prefix`.
   Delete `_sweep_stale_uploads` :24 and its call at :46; replace with a bucket lifecycle
   rule expiring incomplete multipart uploads and the tusd prefix after 24h.

7. **Migration script** (`app/cli.py` subcommand) — walk the existing volume, upload every
   object to its matching key, verify counts and total bytes, and print a summary. Idempotent
   (skip keys that already exist with the same size) so it can be run twice.

### Verification checklist

- [ ] Full round trip on a real bucket: create project → tus upload → analyze → render →
      play a clip in the editor → download it. No file written under `MEDIA` at any point.
- [ ] `grep -rn "FileResponse\|\.stat()\|os.replace\|Path(" app/` shows no remaining media
      filesystem access outside `app/storage.py` and the worker's temp dirs.
- [ ] Key-guard tests mirroring the `_inside()` cases: `..` traversal, absolute key, a
      non-bare clip name, and another user's prefix are all rejected.
- [ ] Cross-account: user A cannot presign or fetch user B's source, clip, waveform or
      thumbnail (the ownership check must run *before* the presign).
- [ ] A presigned URL stops working after `presign_ttl_seconds`.
- [ ] `check_production_ready()` fails with no bucket configured and `ENVIRONMENT=prod`.
- [ ] Storage quota still adds up: `quota.storage_bytes` over a known set of projects matches
      the bucket's actual usage for those prefixes.
- [ ] Delete a project → its prefix is gone from the bucket.
- [ ] Migration script run twice leaves the bucket unchanged the second time.
- [ ] `alembic upgrade head` then `alembic revision --autogenerate` is still empty (CI's
      drift check — Phase A adds no columns, so this must stay clean).

### Anti-pattern guards

- **Do not** stream media through the app as a proxy. Presign and redirect; the point of this
  phase is that bytes stop touching the web process.
- **Do not** presign before the ownership check, and do not presign anything under a prefix
  derived from user input.
- **Do not** let the worker hold a downloaded source outside a `TemporaryDirectory` — a
  crashed job must not leak a 2 GB file into the container.
- **Do not** let `app/storage.py` return a `Path` for media any more. If a caller needs a
  local file, it asks for a download into a temp dir it owns.
- **Do not** keep the local-disk code path "just in case". Two storage backends means two
  sets of bugs and the traversal guards get re-derived wrong in one of them.
- **Do not** widen the tusd hook's trust because the path validation went away. It is still
  an unauthenticated-by-design endpoint reachable only by network position.

---

## Phase B — Scene-aware cut boundaries

**Depends on:** nothing (independent of Phase A).

**Goal:** clip edges land where the *video* cuts, not only where the *sentence* ends. Today
snapping is transcript-only, so a clip can open mid-shot on a frame nobody would choose.
This is the cheapest available quality win and the only one that fits inside both locked
constraints — ffmpeg's scene score is a frame-difference metric, not a model.

### What to implement

1. **`scene_track(video, cfg) -> list[float]`** in `clipfinder.py`, next to
   `loudness_track()` :563 and following its shape (blocking, returns a plain list, no
   engine state). Use the `metadata=print` form from Phase 0, parse `pts_time`, apply the
   downscale mitigation from the start rather than as a follow-up optimisation.

2. **`clipfinder.toml`** — `scene_threshold = 0.4` and `scene_detect = true` under
   `[clips]`, plumbed through `DEFAULTS` (:48) like every other knob. `scene_detect = false`
   must skip the decode pass entirely, so the added CPU is opt-out.

3. **Third boundary set.** `_boundaries()` :613 returns `(starts, ends, pauses)`; give it a
   fourth element `scenes`, or pass the list into `_snap_clip()` :630 directly. The
   integration rule matters more than the plumbing:

   > Scene points join the candidate sets in the **`else`** branches of `_snap_clip` only —
   > `pauses | starts` for the start edge, `pauses | ends` for the end edge. The existing
   > "edge landed inside a spoken segment → grow to the whole sentence" branch still wins
   > first, unchanged.

   That preserves the no-mid-word-cut guarantee the function exists to provide, and improves
   only the case where the edge was already going to land on a pause. `_snap()` :625 needs no
   changes — it is already a generic nearest-point helper.

4. **Persist the scene list.** New `analyses.scenes` JSONB column (nullable, one Alembic
   migration) written by `pipeline.analyze`. Render-time re-snapping and any future
   re-analyze must not trigger another decode pass.

5. **Pass it through `pipeline.analyze`** alongside `loud`, and include it in the analyze
   result so the editor could eventually draw scene ticks on the timeline. Not required this
   phase — but write the column and the payload now, so the frontend work is additive later.

### Verification checklist

- [ ] **Measured** CPU delta for analyze on a 60-min 1080p source, `scene_detect` off vs on,
      with the chosen mitigation. Record the number in this file. If it is worse than ~1
      CPU-min, try the next mitigation before shipping.
- [ ] On a video with hard cuts, detected `pts_time` values line up with the visible cuts
      (spot-check 5 against frame grabs).
- [ ] A clip whose edge previously landed mid-shot now opens on the scene boundary.
- [ ] **No regression on the primary guarantee**: no clip edge falls inside a spoken segment
      on a corpus of at least 10 videos. This is the rule scene points must never break.
- [ ] `scene_detect = false` produces byte-identical clip bounds to the pre-phase code.
- [ ] A video with no detectable scene changes (single static shot) behaves exactly as
      before — empty scene list, no crash, no change in bounds.
- [ ] `alembic upgrade head` + autogenerate is empty (the new column is migrated, not drifted).
- [ ] Threshold sanity sweep at 0.3 / 0.4 / 0.5 on streamer footage; record which was chosen
      and why.

### Anti-pattern guards

- **Do not** let a scene point override the mid-sentence protection. A cut on a shot boundary
  that slices a word in half is worse than the current behaviour.
- **Do not** add the decode pass unconditionally — `scene_detect = false` must cost nothing.
- **Do not** parse ffmpeg's stderr with a regex that assumes a fixed field order; `showinfo`
  output has changed shape between releases. Prefer the `metadata=print` form.
- **Do not** recompute scenes at render time. That is what the `analyses.scenes` column is for.
- **Do not** fold the scene decode into `extract_audio` by removing its `-vn`. Audio
  extraction is cheap precisely because it never touches video; keep the passes separate and
  keep the cheap one cheap.

---

## Final phase — Verification

1. Re-run the full CI matrix (`ruff`, `alembic upgrade head`, the autogenerate drift check,
   the import smoke test, `npm run build`).
2. Update `DEPLOY.md`'s capacity notes: new per-video CPU baseline from Phase B's measurement,
   the storage line moving from Hetzner volumes to R2, and the S3 environment variables.
   **Also correct the infra budget** — Hetzner raised CPX/CCX prices 107–204% on 15 June 2026
   (CCX23 €31.49 → €85.99), so any capacity note written before that date understates the
   server line by roughly 2.7×.
3. Update `.env.example` with the S3 block and the two new `[clips]` knobs.
4. Confirm the cost model still holds: per-user-month should now be **storage on R2 + $0
   egress + unchanged Groq API cost**, with the per-video API cost still ~$0.044 (transcription
   is ~90% of it, and nothing in this plan touches transcription).
5. Note in `PLAN-boost.md` that Phase F (AI thumbnails) remains dark by decision, not by
   omission — constraint 1.

---

## Known-good state at the start of this plan

Commit `dacbbb1` ("Fix the security and resource findings from the code review") closed the
findings from the 2026-09-22/24 review: the tusd hook is unreachable from outside and no
longer trusts its payload; rate limits key on `X-Real-IP` instead of collapsing into one
global bucket; `check_production_ready()` blocks a public boot on dev secrets; the render
quota counts clips rather than jobs; every job exit routes through `_fail()`; the orphan
sweep also clears stuck projects; and `cut_clip` sets `-pix_fmt yuv420p`.

Still open and **not** in this plan — carried so they are not lost:

- `save_edit` accepts an unbounded JSONB blob (no size cap)
- sfx uploads are not counted against the storage quota, and have no per-user file count cap
- `media_frame`'s `t` is unbounded, and the frames cache grows without limit
- The render SSE subscribes before the job row exists, so it can report the *previous*
  render's status — `RenderPanel.tsx` should subscribe to the `job_id` that `/render` returns
- `AppShell.tsx:24` links superusers to `/admin`, which has no route (the `/api/admin/*` API
  is complete; the UI was deferred at `PLAN-multiuser.md:85`)
- 1-hour sessions with no refresh token (`PLAN-multiuser.md:253` calls this a later step)
- `email.py`'s retention warning interpolates a project name into HTML unescaped
- Quota and credit checks are check-then-act, so parallel requests can both pass
- **No test suite.** CI checks lint, schema drift, imports and the frontend build — nothing
  asserts behaviour. The four paths worth covering first: quota enforcement, ownership
  checks, the tusd hook gates, and `keptRanges` ⇄ `_clip_to_range` agreement.
