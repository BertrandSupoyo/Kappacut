import { useEffect, useId, useRef, useState } from "react";
import { useEditor } from "./store";
import { fmtDuration } from "@/lib/format";
import { clipVisualStyle, clipLoopHandlers } from "./clipVisual";
import { ColorGrade, gradeId } from "./colorGrade";
import { faceAt, faceFrame } from "./types";
import { CaptionOverlay } from "./CaptionOverlay";

/** `compact` is for watching, where the clip's start and end can't be set: the agent page's preview. */
export function Player({ compact = false, onVideoError }: { compact?: boolean; onVideoError?: () => void }) {
  const projectId = useEditor((s) => s.projectId);
  const activeIdx = useEditor((s) => s.activeIdx);
  const edit = useEditor((s) => s.edits[s.activeIdx]);
  const setPlayhead = useEditor((s) => s.setPlayhead);
  const playhead = useEditor((s) => s.playhead);
  const markIn = useEditor((s) => s.markIn);
  const markOut = useEditor((s) => s.markOut);

  const vidRef = useRef<HTMLVideoElement>(null);
  const [playing, setPlaying] = useState(false);
  const [muted, setMuted] = useState(false);

  const face = useEditor((s) => s.clips[s.activeIdx]?.face);
  const start = edit?.start ?? 0;
  const end = edit?.end ?? 0;
  // "Follow face": place the preview's crop where the render's crop will be at this moment
  const look =
    edit?.look && edit.look.follow && face?.track
      ? { ...edit.look, ...faceFrame(faceAt(face, playhead), edit.look.mode, edit.look.zoom) }
      : edit?.look;
  const gid = gradeId(useId());
  const vis = clipVisualStyle(look, edit?.color, gid);
  const { vertical, mode, blurFill, cropStyle, bgStyle, fgWrapStyle } = vis;
  // the playhead only ticks ~4x a second; a short glide hides the steps
  const innerStyle = look?.follow
    ? { ...vis.innerStyle, transition: "object-position .3s linear, transform-origin .3s linear" }
    : vis.innerStyle;
  // the blurred backdrop just loops on its own — it's an out-of-focus ambient layer, not
  // something that needs frame-accurate sync with the foreground's play/pause/scrub state
  const bgLoop = clipLoopHandlers(start, end);

  // jump to the clip's start whenever the active clip changes
  useEffect(() => {
    const v = vidRef.current;
    if (v) {
      try {
        v.currentTime = start;
      } catch {
        /* metadata not ready */
      }
    }
  }, [activeIdx, start]);

  // keep the video at the external playhead when scrubbing the timeline
  useEffect(() => {
    const v = vidRef.current;
    if (v && !playing && Math.abs(v.currentTime - playhead) > 0.25) {
      try {
        v.currentTime = playhead;
      } catch {
        /* ignore */
      }
    }
  }, [playhead, playing]);

  function onTime() {
    const v = vidRef.current;
    if (!v) return;
    if (v.currentTime >= end - 0.03) {
      v.pause();
      v.currentTime = start;
      setPlaying(false);
    }
    setPlayhead(v.currentTime);
  }

  function toggle() {
    const v = vidRef.current;
    if (!v) return;
    if (v.paused) {
      if (v.currentTime < start || v.currentTime >= end) v.currentTime = start;
      void v.play();
    } else {
      v.pause();
    }
  }

  const video = (
    <video
      ref={vidRef}
      data-main=""
      src={`/media/${projectId}/source`}
      // phones often show nothing at all until a video plays
      poster={compact ? `/media/${projectId}/frame.jpg?t=${Math.floor(start)}` : undefined}
      style={innerStyle}
      preload="metadata"
      muted={muted}
      onPlay={() => setPlaying(true)}
      onPause={() => setPlaying(false)}
      onTimeUpdate={onTime}
      onError={onVideoError}
      playsInline
    />
  );

  return (
    <div>
      <ColorGrade id={gid} color={edit?.color} />
      <div style={compact && vertical ? { ...cropStyle, maxWidth: "min(210px, 40vh)" } : cropStyle}>
        {blurFill && (
          <video
            src={`/media/${projectId}/source`}
            style={bgStyle}
            preload="metadata"
            autoPlay
            muted
            loop
            playsInline
            {...bgLoop}
          />
        )}
        {mode === "cropfill" && vertical ? <div style={fgWrapStyle}>{video}</div> : video}
        {edit && <CaptionOverlay key={`${mode}-${vertical}`} video={vidRef} edit={edit} vertical={vertical} />}
      </div>

      <div className="transport" role="group" aria-label="Playback">
        <button className="btn ghost icon" onClick={toggle} aria-label={playing ? "Pause" : "Play"}>
          {playing ? (
            <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden><path d="M7 5h4v14H7zM13 5h4v14h-4z" /></svg>
          ) : (
            <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden><path d="M7 5v14l12-7z" /></svg>
          )}
        </button>
        <span className="muted" style={{ fontSize: "var(--fs-xs)", fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap" }}>
          {fmtDuration(Math.max(0, playhead - start))} / {fmtDuration(end - start)}
        </span>
        <span style={{ display: "flex", gap: "var(--sp-2)", marginLeft: "auto", flexWrap: "wrap" }}>
          {!compact && (
            <>
              <button className="btn ghost" onClick={() => markIn(playhead)} title="Set the clip's start to the playhead (I)">
                Set start <kbd>I</kbd>
              </button>
              <button className="btn ghost" onClick={() => markOut(playhead)} title="Set the clip's end to the playhead (O)">
                Set end <kbd>O</kbd>
              </button>
            </>
          )}
          <button className="btn ghost icon" onClick={() => setMuted((m) => !m)} aria-label={muted ? "Unmute" : "Mute"} aria-pressed={muted}>
            {muted ? (
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden><path d="M11 5L6 9H3v6h3l5 4z" /><path d="M22 9l-6 6M16 9l6 6" /></svg>
            ) : (
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden><path d="M11 5L6 9H3v6h3l5 4z" /><path d="M16 9a4 4 0 0 1 0 6" /></svg>
            )}
          </button>
        </span>
      </div>
    </div>
  );
}
