# clipfinder editor v2 — toward CapCut-standard

Phased plan. Each phase is self-contained and can run in a fresh context.
Files in play: `clipfinder.py` (`cut_clip`), `pipeline.py` (`build_ass`, `render`),
`server.py` (endpoints), `web/{index.html,styles.css,app.js}`.

---

## Phase 0 — Allowed APIs & patterns (read before touching code)

### ffmpeg (already the render engine; `cf.FFMPEG`, one subprocess per clip via `cf.run`)
- `split=2[a][b]` — duplicate a stream. Needs `-filter_complex`, not `-vf`.
- `scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920` — cover-fit then hard-crop. **Used today** in `cut_clip` fill branch (clipfinder.py ~line 175).
- `scale=1080:-2:flags=lanczos` — fit width, keep aspect, even height.
- `crop=w=EXPR:h=EXPR:x=EXPR:y=EXPR` — expressions may use `iw ih ow oh`. **Used today** in the crop branch (~line 180).
- `gblur=sigma=N` — Gaussian blur, N≈6–60. `boxblur=luma_radius=N:luma_power=2` is the faster alternative.
- `eq=brightness=-0.06:saturation=1.12` — tint the blurred layer. **Used today.**
- `overlay=(W-w)/2:(H-h)/2` — centre one input on another. **Used today.**
- `subtitles=<bare filename>` run with `cwd=<ass parent>` — the working caption-burn pattern (clipfinder.py ~line 176; do NOT pass an absolute Windows path to `subtitles=`).
- `-map "[v]" -map "0:a?"` with `-filter_complex` — video from graph, audio passthrough; `-af loudnorm` still applies to `0:a`. **Used today** (fill branch).
- Anti-pattern: mixing `-vf` and `-filter_complex` in one command (ffmpeg refuses). Pick one per branch — the current code already branches on `fc is not None`.

### ASS subtitle override tags (inside the `Text` field of a `Dialogue:` line, pipeline.py `build_ass`)
- `{\an5\pos(X,Y)}text` — `\pos` places the line's anchor at pixel `(X,Y)` in `PlayResX × PlayResY` space (`build_ass` sets PlayRes to `1080×1920` or `1920×1080`). `\an` sets which point of the text box the anchor is: `1`=bottom-left … `5`=centre … `9`=top-right. With `\pos`, the style's `Alignment` and `MarginV` are ignored.
- `{\frz<deg>}` rotate, `{\fscx<pct>\fscy<pct>}` scale (karaoke pop already uses `\fscx114`), `{\c&Hbbggrr&}` fill colour, `{\3c&Hbbggrr&}` outline colour, `{\bord<n>}` outline width, `{\shad<n>}` shadow, `{\1a&HNN&}` fill alpha.
- Animation: `{\fad(120,120)}` fade in/out ms; `{\move(x1,y1,x2,y2)}` slide; `{\t(\fscx120\fscy120)}` animate a transform. Reference: <https://aegisub.org/docs/latest/ass_tags/>.
- Colour byte order is `&HAABBGGRR` (alpha, blue, green, red) — note this is **not** web `#RRGGBB`. `CAP_STYLES` already stores `&H00FFFFFF` etc.
- Anti-pattern: don't set both `\pos` and a `MarginV` style expecting them to combine — `\pos` wins.

### Browser (app.js editor)
- `HTMLVideoElement.requestVideoFrameCallback(cb)` — `cb(now, meta)` fires once per presented frame; **re-register inside the callback**. Chrome/Edge, Safari 15.4+, Firefox 132+. Fallback: `requestAnimationFrame`. Already wired in `fillTick` (app.js ~line 470).
- `CanvasRenderingContext2D.drawImage(videoEl, dx, dy, dw, dh)` — draws the *current decoded* frame. Same-origin `<video src="/media/...">` → canvas is **not** tainted, `drawImage` succeeds. Already in `paintFillBg`.
- Forcing a decode while paused: assigning `video.currentTime` (even `v.currentTime = v.currentTime + 0.001`) schedules a seek → decode → `seeked` event. Use this to guarantee a frame exists before the first `drawImage`.
- Pointer drag: `el.setPointerCapture(e.pointerId)` + `pointermove`/`pointerup` on `window` — the pattern already used for `#tl-in/#tl-out` and `#crop-box` (app.js ~line 275, ~line 445).
- Anti-pattern: relying on `timeupdate` (≈4 Hz) to drive a canvas mirror — use `requestVideoFrameCallback`.

---

## Phase 1 — Blur background must never be black  ✅ DONE

`server.py` `/media/{job}/frame.jpg?t=` (cached); `#fill-still` CSS-blurred layer under the
canvas in `styles.css`/`app.js`; `#video` bg set transparent in fill mode; preview blur ≈ sigma × 1.8.
Verified: bars show blurred video paused / seeking / playing; no console errors.

---

## (original Phase 1 detail kept below for reference)

## Phase 1 — Blur background must never be black

**Problem:** in fill mode the top/bottom bars render black until (and unless) the `<canvas>` mirror paints. Headless can't composite video→canvas; some real-browser states (paused, just-loaded, pre-first-frame) also show black.

### 1a. Backend: a blurred poster the bars can always fall back to
- In `server.py`, add `GET /media/{job_id}/frame.jpg?t=<seconds>`:
  - resolve source via `_source_path(job_id)` (pattern: `media_source`, server.py ~line 225).
  - `cf.run([cf.FFMPEG, "-y", "-ss", str(t), "-i", src, "-frames:v", "1", "-vf", "scale=400:-2", out])` — copy the arg style from `cf.grab_frame` (clipfinder.py ~line 171).
  - cache to `web_data/<job>/frames/<int(t)>.jpg`; serve with `FileResponse(..., headers={"Cache-Control": "public, max-age=86400"})` — copy the header pattern from `media_waveform` (server.py ~line 233).
- Anti-pattern guard: do **not** regenerate on every request — check the cache file first (as `media_waveform` does).

### 1b. Frontend: layer a CSS-blurred still under the live canvas
- `index.html`: inside `#video-wrap`, keep `<canvas id="fill-bg">`; add `<div id="fill-still"></div>` **behind** it (before it in DOM).
- `styles.css`: under `.video-wrap.fill`, `#fill-still` = `position:absolute; inset:0; background-size:cover; background-position:center; filter: blur(var(--blur)) brightness(.88) saturate(1.12); transform: scale(1.16); z-index:0;` and `#fill-bg { z-index:1 }`.
- `app.js` `mountFillBg()`:
  - set `#fill-still` background to `url(/media/${S.jobId}/frame.jpg?t=${Math.round(S.sel.start + 1)})` immediately (always shows *something* blurred).
  - after `v.currentTime` settles, refresh the still `t` to the playhead on `seeked` (debounced ~400 ms) so a paused frame still roughly matches.
  - keep `fillTick` (rVFC canvas) for smooth motion during playback; on `pause`, do one `v.currentTime = v.currentTime` nudge to force a `seeked` → `paintFillBg` so the last frame is drawn.
  - remove the `::after` "blurred video fills the bars on export" hint text — no longer needed once a still is always present. Keep the `.live` class only to fade the canvas in over the still.

### 1c. Match preview blur ↔ export blur
- Today: CSS `blur(Npx)` in preview, `gblur=sigma=N` on export, same `N`. They are **not** visually 1:1 (CSS px ≈ ~2× gblur sigma at these sizes). Calibrate once: render `sigma=10,20,30,40` frames, eyeball which CSS px matches, store a factor (e.g. `cssBlurPx = sigma * 1.8`) and apply it only to the preview `--blur`. Keep the slider value = sigma (what the backend gets).

### Verification
- `curl 'http://127.0.0.1:8000/media/<job>/frame.jpg?t=30' -o f.jpg` → valid JPEG, second call is instant (cached).
- Open the editor in a **real browser**, 9:16 → Fill · blur: bars show blurred video immediately, while paused, and while playing. Never solid black.
- `grep -n "fill-still" web/*.js web/*.css web/*.html` → wired in all three.
- Render a fill clip, `ffprobe` → still `1080x1920`; frame grab → bars visibly blurred (already passing).

---

## Phase 2 — Captions: drag anywhere + free text  ✅ DONE

`S.cap.pos {x,y}` per-clip in `S.capPos`; drag `#cap-ov > span` (pointer capture); `build_ass`
takes `pos=` → `{\an5\pos(X,Y)}` prefix on every Dialogue line (karaoke too); `#cap-pos` presets
removed; `cap-head` shows `x% · y%` + reset; hand-typed lines get `− + ×` time-nudge buttons.
Verified: drag persists, `+ line` reaches the queue with the position, render places the caption
where dragged, no console errors.

---

## (original Phase 2 detail kept below for reference)

## Phase 2 — Captions: drag anywhere + free text

**Goal:** a caption is a positioned, editable text object. Keep the 4 style presets and karaoke; replace the 3 position presets with a draggable anchor; keep phrase auto-split; allow fully hand-typed lines.

### 2a. State (`app.js` `S.cap`)
- Replace `position: "bottom"` with `pos: { x: 0.5, y: 0.86 }` (normalised 0–1, anchor = centre of the text box). `x/y` is per-clip, stored on `S.capPos[clipKey()]` so each clip remembers its placement; default `{0.5, 0.86}`.
- Keep `style`, `size`, `karaoke`, `enabled`. Add `rot: 0` (optional, degrees) if rotation is wanted later — leave the field, don't build the UI yet.

### 2b. On-video drag (`app.js`, new, mirror the `#crop-box` drag)
- `#cap-ov > span` becomes the draggable handle when captions are on and not playing-locked: `pointerdown` on the span → `setPointerCapture`, record `startX/Y` + `S.cap.pos`, `pointermove` updates `pos` from delta / `#video-wrap` rect, clamp `0.04..0.96`, `pointerup` persists to `S.capPos`.
- `updateCaptionOverlay()` (app.js ~line 680): position the span with `left: calc(x*100%)`, `top: calc(y*100%)`, `transform: translate(-50%,-50%) scale(<fit>)`. Drop the `.p-bottom/.p-center/.p-top` classes and their CSS rules (styles.css ~line 319–321).
- Keep the "shrink to fit one line" scale logic already added (app.js ~line 695).
- Add a faint dashed box + move-cursor on hover so it reads as draggable.

### 2c. Caption text panel (`#cap-lines`, already phrase-based)
- Already: one editable `<input>` per phrase, `+ line` adds a hand-typed line, `×` removes an added line (app.js `renderCaptionEditor`). Keep.
- Add per-line: a small time nudge (`−`/`+` 0.1 s on start) for hand-typed lines, and a drag-to-retime handle is out of scope for v2 (note it in Phase 5).
- Add a "Position" readout in the caption panel header showing `x% / y%` and a "reset" that sets `pos` back to `{0.5, 0.86}`.

### 2d. Backend `build_ass` (`pipeline.py` ~line 149) — accept a position
- Signature: add `pos: dict | None = None` (`{"x","y"}` 0–1). Drop reliance on `position: str` / `_ALIGN` / `margin_v` when `pos` is given.
- In the header `Style:` line keep `Alignment` = `5` (centre anchor) when `pos` is set.
- In `dlg(a, b, text)`: when `pos`, prefix every line with `{\an5\pos(%d,%d)}` % `(round(pos["x"]*play_x), round(pos["y"]*play_y))`. When no `pos`, keep today's behaviour (back-compat for the CLI batch path).
- Karaoke branch: the `\pos` prefix goes on each per-word `Dialogue` line too (before the existing `{\c...}` word override).
- `render()` (`pipeline.py` ~line 220): read `cap.get("pos")` from the range's `captions` dict and pass it through. `capsForQueue()` / `phrasesFor` in app.js already build `captions` — add `pos: S.cap.pos` there.

### 2e. Optional within v2: text look controls
- Add a colour swatch row (fill + outline) and an outline-width slider to the caption bar, stored on `S.cap.fill` / `S.cap.oc` / `S.cap.bord`. Map to ASS `{\c&H..&\3c&H..&\bord<n>}` prefix and to CSS `color` / `-webkit-text-stroke` in the overlay. Convert `#rrggbb` → `&HAABBGGRR` with a 6-line helper. If time-boxed, ship 2a–2d first and do 2e in Phase 4.

### Verification
- Drag the caption on the preview → it moves; switch clip and back → position restored.
- `+ line`, type text, it appears on the preview at the caption position and in a render.
- Render with a moved caption → open the `.ass`: every `Dialogue:` line starts with `{\an5\pos(...)}`; the burned caption in the mp4 is where you dragged it (grab a frame, eyeball).
- `grep -n "_ALIGN\|p-bottom\|p-center\|p-top" web pipeline.py` → only dead/back-compat references remain, none on the drag path.
- CLI `python clipfinder.py <video>` still renders (no `pos` passed → old margin behaviour) — run it once.

---

## Phase 3 — Hybrid 9:16: crop the real subject + blurred background  ✅ DONE

`crop.mode "cropfill"` — `cut_clip` shares the fill filter-complex, foreground = `crop` of the
source (aspect from `crop.aspect`, `_aspect_ratio()` helper) scaled to 1080 width, overlaid on
the blurred bg. UI: `#vmode` third button "Subject + blur", `#vaspect` chips 16:9 / 4:5 / 1:1,
`cropBoxFrac()` aspect-aware, `_subject` filename tag, queue label "· 9:16 subject".
Verified: 4:5 and 1:1 renders show the panned/zoomed subject with blurred bars + captions
placed on top; no console errors.

---

## (original Phase 3 detail kept below for reference)

## Phase 3 — Hybrid 9:16: crop the real subject + blurred background

**Goal:** a third `crop.mode` (`"cropfill"`) — the manual reframe box picks a region of the *real* footage, that region is scaled to the 1080 width and centred, and the leftover top/bottom is the blurred-video background (not black). This is CapCut's "canvas + blur" for a subject you framed yourself.

### 3a. Crop box aspect selector (`app.js` + `index.html`)
- Today `#vmode` = `Crop | Fill · blur`. Add a third: `Crop | Fill | Subject + blur`.
- When `mode === "cropfill"`, show the crop box (reuse `#crop-box`, pan + zoom) **and** the blur slider. Add an aspect chip group `[ 16:9 · 4:5 · 1:1 · Free ]` that sets `S.crop.aspect` (default `"16/9"` = the source, which is what produces visible bars).
- `cropBoxFrac()` / `positionCropBox()` (app.js ~line 377): derive the box height from `aspect` instead of the fixed `1/zoom` used for pure 9:16. Box width fraction = `min(1, (aspectW/aspectH) * (wrapH/wrapW) / zoom)`, height = width * (aspectW/aspectH) * (wrapW/wrapH)... — compute against the 16:9 `#video-wrap` so the box overlays the real frame correctly. Keep pan `px/py` as "fraction of travel".
- Preview: same `.video-wrap.fill` 9:16 shell as Phase 1, but the **foreground** `#video` is `object-fit: cover` inside a sub-box positioned/sized to the crop rect (a wrapper div `#cropfill-fg`), and `#fill-still` + `#fill-bg` sit behind exactly as in Phase 1.

### 3b. Backend `cut_clip` (`clipfinder.py` ~line 156) — new branch
- Add `elif mode == "cropfill":` between `fill` and the plain `crop` branch. Build a `-filter_complex` (set `fc`, like the `fill` branch):
  ```
  [0:v]split=2[a][b];
  [b]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,gblur=sigma={blur},eq=brightness=-0.06:saturation=1.12[bg];
  [a]crop=w={bw}:h={bh}:x=(iw-{bw})*{px}:y=(ih-{bh})*{py},scale=1080:-2:flags=lanczos[fg];
  [bg][fg]overlay=(W-w)/2:(H-h)/2[v]
  ```
  where `bw`/`bh` are the crop-rect width/height **as ffmpeg expressions in source px** derived from `aspect` + `zoom` (analogous to the existing pure-9:16 `bw = "ih*9/(16*z)"`, but with the chosen aspect and, for `cropfill`, no requirement to reach `ih`).
- Reuse the `sub_fc` / `map_v` handling already in the `fill` branch verbatim.
- `pipeline.py` filename tag: `_cropfill` (extend the `vtag` ternary, pipeline.py ~line 219).

### 3c. `cropSnapshot()` / `cropKey()` (app.js ~line 431)
- `cropSnapshot` for `cropfill` returns `{ mode:"cropfill", px, py, zoom, aspect, blur }`.
- `cropKey` adds the aspect + blur so distinct hybrid framings are distinct queue entries.
- Queue row label (app.js `renderQueue` ~line 588): `· 9:16 subject`.

### Verification
- API render with `crop:{mode:"cropfill", px:0.3, py:0.4, zoom:1.2, aspect:"16/9", blur:26}` → `1080x1920` mp4; frame grab shows the panned/zoomed subject region centred, blurred fill above/below (not black).
- `aspect:"9/16"` (or zoom high enough that the box reaches full height) → foreground fills, no bars — must not error, just degrade to the pure crop.
- Preview: crop box overlays the real frame; moving it changes the centred region; blur bars visible behind.
- `grep -n "cropfill" clipfinder.py pipeline.py web/app.js` → present in all.

---

## Phase 4 — Position & polish pass  ✅ DONE

Vertical controls (`#vmode`/`#vaspect`/`#vblur`) moved to their own `.vrow` under `.tl-controls`,
shown only when 9:16 is on; `.cap-panel` sticky; `.cap-head` mini buttons shrunk; `/api/recent`
backfills `name.txt` from `analysis.json`'s `video` field (3 pre-tracking jobs still show
`source.mp4` — original name genuinely lost); keyboard `,`/`.` = frame step, `[`/`]` = caption
nudge (Shift = horizontal). Verified: no overflow at 390 px, `[` twice moves `cap.pos.y` 0.86→0.82,
no console errors.

---

## (original Phase 4 detail kept below for reference)

## Phase 4 — Position & polish pass

- **Editor layout:** the right rail (caption panel + queue + gallery) is tall; make caption panel sticky while the gallery scrolls. Verify at 1280 / 1024 / 390 widths (media queries already at 1100/760/430).
- **`#vmode` / blur / aspect row** wraps awkwardly when all controls show — put the vertical controls on their own line under `.tl-controls` when 9:16 is on.
- **Caption drag vs. native video controls:** the drag handle must not swallow clicks on the `<video controls>` scrubber — only start a caption drag from the text span, and `e.stopPropagation()`.
- **Fill preview size:** `width: 300px` fixed — make it `min(300px, 42vh)` so it fits shorter screens without pushing the timeline below the fold.
- **`_ALIGN` / `.p-*` cleanup** once Phase 2 lands (leave the CLI back-compat path).
- **Recent panel names:** old jobs show `source.mp4`; add a one-off backfill — on `/api/recent`, if no `name.txt`, fall back to reading `analysis.json`'s `video` field before `source.<ext>`.
- **Keyboard:** add `[` / `]` to nudge the caption position, `,` / `.` to step one frame (video is `controls` now, but power users expect these).

### Verification
- Resize sweep 1280→390: no element overflows its panel, timeline stays above the fold on a 800px-tall viewport.
- Click the video scrubber while a caption is on-screen → scrubs, does not drag the caption.

---

## Phase 5 — CapCut-standard roadmap (build after 1–4, in this order)

Ranked by value ÷ effort for this codebase (ffmpeg + vanilla JS, single-user, local):

1. **Zoom/pan keyframes ("Ken Burns" / punch-in on a beat)** — let the crop rect have 2+ keyframes over the clip; ffmpeg `crop` with `t`-driven expressions or `zoompan`. Turns the static reframe into motion. *Medium.*
2. **Caption animation presets** — pop-in / typewriter / bounce / slide, via ASS `\t` `\move` `\fad` prefixes chosen from a dropdown. *Low.* (Pairs with Phase 2.)  ✅ DONE — `S.cap.anim` (`none`/`pop`/`fade`/`slide`/`bounce`), `#cap-anim` seg-btns in the caption bar. `pipeline.py` `_anim_lead()` builds the leading override (`\fad`/`\fscx..\t(..)` / `\move` for slide; karaoke gets only `\fad`). Preview: CSS `@keyframes` (`capPop/capFade/capSlide/capBounce`) re-triggered on phrase change via `playCaptionAnim()`. Persists in `project.json`. Verified: render (libass accepts the tags, caption scales in) + browser (class toggles, restore).
3. **Multi-clip stitch** — combine 2–3 kept highlights into one vertical video with a chosen transition (`xfade` filter: fade / wipe / slide / zoom). New "Compilation" item in the render queue. *Medium.*
4. **Background music track** — upload/pick an mp3, `amix` with `sidechaincompress` ducking under speech, trim to clip length, loudnorm the mix. *Medium.*
5. **Speed control** — per-segment `setpts`/`atempo` (0.5×–2×), with a speed-ramp option. *Low–medium.*
6. **Auto-reframe (subject/face tracking)** — the crop rect follows the speaker. Needs a detector; on Windows-with-SAC prefer a WASM face detector (mediapipe-tasks-vision via CDN) run in the browser at analyse time, store a per-second centre track, feed keyframed `crop x` to ffmpeg. *High — do a spike first: confirm the WASM model loads under SAC.*
7. **Colour presets / adjust** — `eq` + `curves`/`lut3d` filters, 4–6 named looks + brightness/contrast/saturation sliders. *Low.*  ✅ DONE — `clipfinder.py` `_color_chain()` + `_COLOR_PRESETS` (none/warm/cool/punchy/film/mono via `colortemperature`/`curves`/`hue`/`eq`), `cut_clip(color=)` appends it before `subtitles=` in all three branches (fill mode grades `[fg]` only, not the blurred bars). `pipeline.render()` passes `r["color"]`. `app.js`: `#look-panel` in the rail — 6 preset chips + Bright/Contrast/Saturation sliders + reset; `applyColorPreview()` mirrors it as a CSS `filter` on `#video` (stacks preset + sliders like the backend). `S.color` global, `colorSnapshot()` into the queue item (`· <preset>` / `· graded` label), `project.json` `lastClip.color`. Verified: mono render SATAVG 8.6→0, warm SATAVG→13; browser preview/queue/persist/restore/reset, 0 errors.
8. **Export presets** — TikTok / Reels / Shorts / YouTube-vertical buttons that set resolution, fps cap, bitrate target and (already) −14 LUFS, plus a safe-zone overlay in the preview. *Low.*
9. **Sticker / logo overlay** — a PNG positioned + timed over the video (`overlay` with `enable='between(t,..)'`). *Low.*

**+ Sound effects** (user ask, not in original ranking)  ✅ DONE (iteration 1: manual placement + basic auto) — shared library at `web_data/_sfx/` (5 built-ins synthesised once via lavfi: whoosh/impact/pop/ding/riser) + user upload. `server.py` `GET`/`POST /api/sfx`, `GET /media/sfx/{name}`. `clipfinder.py` `mix_sfx()` — second ffmpeg pass, `adelay`+`volume` per hit → `amix normalize=0` + `alimiter`, video copied. `pipeline.render()` takes `sfx_dir`, mixes when a range has `sfx:[{file,at,gain}]`. `app.js`: `#sfx-panel` in the editor rail — library chips (▶ preview, + drop at playhead), upload, placed list with −/+/× and time, amber diamond markers on the timeline, "Auto hit" toggle (whoosh on entrance), SFX fire in sync during `Preview`. `S.sfxAdds`/`S.sfxAuto` per clip key, in `project.json`. Verified: 1240 Hz ding shows +17 dB at its placement time in the render; browser end-to-end (place, marker, queue `· FX`, persist, render `sfx:3`). *Next: auto-place on beats / caption emphasis, per-hit gain slider, drag markers on the timeline.*
10. **Project persistence** — save the whole edit (queue + caption text/positions + crop rects) to `web_data/<job>/project.json`, reload from the Recent panel into a ready-to-render state. *Low, and it makes everything above worth doing.*  ✅ DONE — `server.py` `GET`/`POST /api/jobs/{id}/project`; `/api/recent` adds `edited`. `app.js` `projectState()`/`applyProjectState()`, debounced `scheduleSave()` on every mutation (queue, kept, caption text/pos/style, reframe), `loadProject()` in `loadResult()` restores + toasts, `#ws-save` "Saved / Saving…" pill, `pagehide` `sendBeacon` flush, Recent rows show "↻ resume edit". Verified end-to-end in a real browser (edit → autosave → reload → restored).
11. **Undo/redo** — a command stack over `S` mutations (queue add/remove, caption edits, crop changes). *Medium; higher once there are more edit types.*
12. **Word-drag caption retiming** — drag a phrase on a mini-waveform to shift its start/end. *Medium.*

Not recommended for this tool: a full multi-track timeline UI, transitions library beyond `xfade`, in-browser GPU effects — they fight the "one ffmpeg pass per clip" model that keeps this thing simple and fast.

---

## + UI positioning / glassmorphism pass  ✅ DONE

Ran `ui-ux-pro-max` (`--domain style` glassmorphism, `--domain ux` progressive-disclosure).
Editor right rail was **5 stacked full-glass panels** (cap/sfx/look/queue/gallery) + an
overloaded caption toolbar (2 toggles + 4 style + 5 anim + slider in one wrap row).

Fixes:
- Rail → **2 elevated glass panels**: `#inspector` (tabbed: Caption / Sound / Look) + `#output` (queue + rendered). `setupInspectorTabs()` toggles `.tab-pane[hidden]`.
- Glass discipline: `.glass` = one elevated tier only; new `.sub` class (`--sub-bg`, hairline border, **no blur/shadow**) for nested surfaces (caption text box, sfx list) — matches the glassmorphism "content on a separate layer" rule.
- Caption toolbar → labelled `.field` groups (Style / Entrance animation / Size); 5–6-button groups use `.seg-btns.wrap` = 3-col grid.
- Spacing scale tokens `--sp-1..6` (4/8/12/16/24/32) replace ad-hoc values + inline `style="padding:…"`.
- ws-top wordmark: solid `--text` instead of the near-invisible chrome gradient on glass.
- Mobile: `.ed-head` wraps cleanly, rail single-column < 760px.
- Verified real browser 1440 + 390: 0 horizontal overflow, tabs + all controls + queue/render flow work, 0 JS errors.

## + Render progress  ✅ DONE

`POST /api/jobs/{id}/render` is now a `BackgroundTasks` job (was blocking); returns `{started, total}`.
`pipeline.render()` takes `on_clip` / `on_stage` callbacks. `_run_render()` fills `JOBS[id]["render"]
= {status, done, total, current, error, clips}`. SSE at `GET /api/jobs/{id}/render/events`.
`app.js`: `streamRender()` — `#render-prog` bar in the output panel (0→100%), per-clip stage text,
button "Rendering N/M…", clips appended to the gallery **as each finishes**, restores on done/error.
Verified in a real browser: 3-clip queue → bar 0/33/67/100, gallery grew 3→4→5→6 one at a time, 0 JS errors.

## + Audio / transport fix  ✅ DONE

Root cause of "no sound in the editor": `.crop-ui` (the 9:16 reframe overlay, `inset:0`) had no
`pointer-events` rule, so in Crop / Subject+blur mode it sat on top of the `<video>` and swallowed
every click on the native control bar — play, volume, scrub all dead. Source files are fine
(all have aac stereo; `/media/{job}/source` serves 200 + `Accept-Ranges` + 206 on Range).

Fixes:
- `.crop-ui { pointer-events: none }`, `.crop-box { pointer-events: auto }` — overlay lets clicks through, only the box grabs.
- **App-level transport** `#vtransport` under the video (always reachable, never covered): play/pause, mute toggle (icon swaps), volume slider, `cur / dur` time. `setupTransport()` wired to `#video` events. Keyboard `m` = mute (space already = play/pause).
- Review preview: dropped the blocked `autoplay` attribute; call `videoEl.play()` inside the click handler so audio-autoplay is permitted.
- Verified real browser: mute/unmute/volume/`m`-key all drive `#video`, transport reachable with 9:16 crop on, 0 JS errors. (Headless can't decode h264 so actual playback audio is unverifiable there — logic is correct.)

## + Logo → home + UI bug pass  ✅ DONE

- **Logo is now a button → home.** `nav .logo` and `.ws-top .logo` are `<button id="nav-logo"/"ws-logo">`.
  `goHome()` shows landing, hides workspace, un-hides `<nav>`, refreshes Recent, scrolls to top (soft —
  no reload, the project is autosaved). `openWorkspace()` hides `<nav>` (ws-top is the header in-app);
  nav-logo scrolls-to-top when already home. `button.logo` style reset + hover/active/focus-visible.
- **Mobile horizontal overflow on the landing (78px)** — `.hero` grid used `1fr` (= `minmax(auto,1fr)`)
  so a non-shrinking `<input>` (no `min-width:0`) forced the track to min-content. Fixed: `.hero > * { min-width: 0 }`,
  `.pick-filter, .pick-path input { min-width: 0 }`, media-query grids use `minmax(0,1fr)`. Verified 0 overflow at 360/390/768/1024.
- **Dead CSS removed** — `.grid3`, `.panel > .p-head`, `.p-body`, `.hl*` (orphaned by the review + rail
  refactors; 0 elements matched them).
- Verified real browser: logo→home from Review/editor/mobile all return to landing, nav toggles correctly, 0 console errors.

## + SaaS polish (chosen: design + product completeness, stays local/no-auth)

### Design system foundation  ✅ DONE
`:root` now has full scales: `--sp-1..6`, `--r-xs/sm/md/lg/pill`, `--fs-2xs..3xl` + `--lh-*`,
`--elev-1..3`, `--dur-fast/base/slow` + `--ease-out`, `--focus`, and semantic aliases
(`--surface`, `--border`, `--accent`, `--danger`, `--success`, `--warn`, `--on-accent`).
Old tokens (`--radius`, `--shadow`, `--fluid`) kept as aliases. Body → `--fs-base`/`--lh-base`.
Global `:focus-visible` ring + a blanket `prefers-reduced-motion` clamp. Border-radii consolidated
to the token scale (`14/15/16px → --r-md`, `12/13 → --r-sm`, `8 → --r-xs`). Fixed undefined
`--violet-2` (crop box border was falling back to currentColor). Verified 0 overflow / 0 errors.

### Settings page  ✅ DONE
`server.py`: `GET`/`POST /api/settings` — overlays `web_data/_settings.json` on top of
`clipfinder.toml` (never rewrites the toml). `pipeline.get_cfg()` merges the overlay.
Keys go to `.env` via `_write_env()` (preserves other lines, skips empty/masked values).
`_source_dirs()` also reads `_settings.json` `source_folders`. Fields: backend (groq/claude),
groq/claude model, temperature, clips-per-video, taste, loudnorm, vision, source folders,
API keys (masked, "leave blank to keep"). Reachable from nav ("Settings") and the ws-top gear.
Verified end-to-end: populate → change → save → `get_cfg()` reflects it → `.env` intact → back nav.

### States + polish  ✅ DONE
- `.empty` + `.skel` system (theme-consistent: glass tint + violet sweep, respects reduced-motion via the global clamp).
- Empty states: render queue ("Trim a clip, then Add to queue"), gallery ("Your rendered clips show up here"); `refreshGallery()` keeps `g-count` = real card count.
- Loading skeletons: pick list, recent list (panel shows with skeleton on first load, hides only if truly empty), gallery, waveform (`new Image()` preload → swap in on load, `.skel` on `#timeline` meanwhile).
- **`?` keyboard-shortcuts modal** — `#kbd` glass card, opened by `?` / the `#kbd-open` icobtn in `.ed-head`, closed by Esc / backdrop / ✕. Editor keydown handler bails while it's open.
- **Browse pollution fix** — dropped `.ts` from `VIDEO_EXTS` (collided with TypeScript source; the cloned dev repos flooded the picker: 200 junk entries → 17 real videos). Added `.claude`/`dist`/`build`/`.next`/`.venv`/`appdata` to skip-dirs + a 200 KB file-size floor.

### Landing + onboarding  ✅ DONE
- New `#features` section ("Everything the cut needs, in one editor") — 6 `.feat` cards (captions, 9:16 modes, SFX, colour, loudness/voice, autosaved projects) with violet icon chips, on the existing `.card.glass` pattern.
- New `#faq` section ("Questions") — 6 native `<details>` accordions (video privacy, API key, formats, edit-after, output location, hosted?). Footer gains Settings + FAQ links.
- `.chrome` wordmark — darker gradient mids + a dark blur halo so it reads on the bright lilac field (kept the metallic effect).
- First-run onboard hint `#onboard` in the editor (dismiss → `localStorage.cf_onboard`, wrapped in try/catch).
- Verified: 0 overflow at 1440 / 390, FAQ toggles, footer/nav settings links, onboard show+dismiss+persist, 0 JS errors.

**SaaS-polish pass complete** (design tokens · settings · states/skeletons · shortcuts modal · browse fix · landing · onboarding). Theme unchanged — Nighty Night gradient, Liquid Glass, violet/cyan, chrome wordmark all intact.

### Trim + wording + box-ratio pass  ✅ DONE
- **Removed low-value UI:** the fake "Highlights found" `.peek` mockup card (hero is now a centred single column, `.hero-in` max-width 680), the fabricated `#proof` testimonials section, the redundant `#ws-status` "N cuts" pill (folded into `#ws-dur`), and one of the two `.subline` claims. Nav "Creators" → "Features". Deleted the orphaned CSS (`.peek*`, `.tcard*`, old 2-col hero + its media queries).
- **Loading copy:** "Linking file…" → "Adding your video…", render "Starting…" → "Preparing…", "Restored your edit" → "Back where you left off", "Scanning your folders…" → "Finding your videos…", and the analysis stages ("reading video" → "Reading the video", "transcribing (Whisper)" → "Transcribing every line", "finding highlights (model)" → "Ranking the moments", "checking coherence" → "Dropping the word-salad clips", "vision pass" → "Looking at a frame from each clip", "Rendering clip N of M").
- **9:16 box ratio:** the fill-blur preview was a tiny 300px box lost in a ~950px panel. Now `min(340px, 52vh)` + a 1px ring + `--elev-2`, and `.editor.v916` adds a soft radial glow behind it so the portrait viewer reads as a deliberate stage. `.crop-lbl` shortened ("drag to reframe · dbl-click resets" / "4:5 → 9:16") + ellipsis guard so it can't spill the box.
- Verified: 0 overflow 1440/390, nav anchors resolve, editor `.v916` toggles, fill box 340px, 0 JS errors.

## + Whole-pipeline auto: energy-driven picking + per-clip sound & effects  ✅ DONE

### Moment picking
- **Loudness envelope** — `cf.loudness_track(audio)` runs one ffmpeg pass (`aresample=8000,asetnsamples=8000,astats=reset=1`) → one RMS-level (dB) per second. `pipeline.analyze()` computes it right after audio extract ("Reading the energy", 28%).
- **Energy-weighted score** — `_finalize()` takes `loud`; per clip `_clip_energy()` finds peak / payoff loudness + the loudest-second offset; `_loud_stats()` gives p40/p75/p92. Score gets `(payoff_lift − 0.4)·2.0` clamped to −0.35 … +1.6 — a real reaction spike near the payoff lifts a clip, a flat moment dips slightly.
- **Prompt** — `PICKER_BRIEF` now tells the model the best clips are energy spikes with a quotable line beside them, and gives `score` rubric weights.

### Sound + effects per clip
- `_auto_edit(c, energy, stats)` → a ready edit stored as `clip.auto` in `analysis.json`:
  `{vertical, crop:{cropfill 16/9, zoom 1.12 hot / 1.05 calm, py 0.42}, caption:{bold+pop+karaoke hot / clean+fade calm}, color:{punchy hot / none calm}, sfx:[whoosh@0, impact/ding at the loudest second]}`. Pure heuristic from the measured envelope — no extra model call, still free.
- **Frontend** — `maybeApplyAuto(c)` in `loadClip()` seeds `S.vertical / S.crop / S.cap / S.color / S.sfxAdds[key]` on first visit (once). `#auto-badge` "✨ AI edit" in the ed-head toggles it: strip → plain, click again → re-apply. `S.autoApplied` / `S.autoOff` persist in `project.json`.
- **Settings** — "Auto-style clips" toggle (`auto_style`, default on) via `_WEB_PREFS`; `S.autoStyle` loaded at boot.
- Verified: real analysis → `analysis.json` clips carry `auto` (loud IShowSpeed clips → hot: bold/pop/karaoke/punchy + impact SFX at the peak second); editor applies it, badge strips/re-applies, queue item carries the styling into render. 0 JS errors. Energy differentiates loud (−20 dB payoff → hot) vs quiet (−33 dB → calm).

## + Mixed Claude models + per-video spend cap  ✅ DONE

Motivation: with `backend = claude`, put the expensive model only where reasoning is hardest
(stage 2, which is tiny in tokens) and keep the big transcript scan on a cheaper model —
and give the whole thing a hard USD ceiling per video.

- **Per-role model** — `DEFAULTS` gains `verify_model` + `vision_model` (both `""` = "same as
  `claude_model`"). `cf._verify_model(cfg)` / `cf._vision_model(cfg)` resolve them. Stage 1
  (`_find_clips_claude`) uses `claude_model`, stage 2 (`_verify_and_refine`) uses `verify_model`,
  the vision pass uses `vision_model`. Recommended combo: Sonnet pick + Opus flow-check + Haiku vision
  (~$0.09/video vs ~$0.19 all-Opus).
- **Spend ledger** — `cf.budget_reset(cfg)` at the top of `analyze()` / `process_video()` starts a
  fresh `cfg["_spend"] = {usd, cap, events, fellback}`. `_budget_charge()` adds each call's real
  `usage` cost (via `COST_PER_MTOK`, already keyed by model → mixed spend sums correctly).
  `_affordable(cfg, model, in_tok, out_tok, stage)` estimates a call before it runs; over the cap it
  returns False and the stage **falls back to free Groq** (pick + flow-check) or **skips** (vision).
  `claude_budget` default `0.0` = no cap (today's behaviour unchanged).
- **`_find_clips_claude` max_tokens** 16000 → `min(12000, 2000 + count*400)` (real output ~1.5k).
- **Surfaced** — `analyze()` result carries `spend: {usd, cap, fellback, models}` when a cap is set
  or Claude ran; editor header (`#ws-dur`) shows `~$0.04` (`(hit budget)` when it fell back). CLI
  prints `Claude spend: ~$0.041 / $0.12 cap  (claude-opus-5, claude-sonnet-5)`.
- **Settings** — AI-model card adds Flow-check model / Vision model selects ("Same as Claude model")
  + a Claude-budget slider (0–$0.50, 0 = off). The whole Claude sub-group dims when backend = Groq.
  New `_SETTINGS_KEYS`: `verify_model`, `vision_model`, `claude_budget`. CLI: `--verify-model`,
  `--vision-model`, `--claude-budget`.
- Verified: helper unit checks (cap math, fallback flag, model resolution, max_tokens curve);
  `/api/settings` round-trip persists all three keys; headless settings-page check — selects
  populated, budget label live, groq-mode dims the Claude rows, 0 overflow, 0 JS errors. Full
  end-to-end Claude run not exercised (no `ANTHROPIC_API_KEY` set); Groq path unchanged (no `spend`
  key, no budget code touched).

## Final Phase — Verification

1. `python -c "import ast; [ast.parse(open(f).read()) for f in ('clipfinder.py','pipeline.py','server.py')]"` and `node -e "new Function(require('fs').readFileSync('web/app.js','utf8'))"` — both clean.
2. Full browser walk-through in a **real** browser (not headless): pick video → Review → Editor → for one clip: drag caption, type a custom line, try each of the 3 vertical modes, confirm blur bars are never black, add to queue, render, play the result with sound.
3. `grep -n "requestVideoFrameCallback\|\\\\pos(\|cropfill\|fill-still" web pipeline.py clipfinder.py server.py` — every new mechanism present where expected.
4. CLI regression: `python clipfinder.py <one video> --n 4` renders 4 clips, captions burn (old margin path), loudnorm applied.
5. `ffprobe` each of: horizontal, 9:16 crop, 9:16 fill, 9:16 subject+blur → correct dimensions, audio stream present, ~−14 LUFS.
