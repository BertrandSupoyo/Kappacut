import { useEffect, useRef, useState } from "react";
import { useEditor } from "./store";
import { fmtDuration } from "@/lib/format";
import { clipVisualStyle, clipLoopHandlers } from "./clipVisual";

export function Player() {
  const projectId = useEditor((s) => s.projectId);
  const activeIdx = useEditor((s) => s.activeIdx);
  const edit = useEditor((s) => s.edits[s.activeIdx]);
  const setPlayhead = useEditor((s) => s.setPlayhead);
  const playhead = useEditor((s) => s.playhead);

  const vidRef = useRef<HTMLVideoElement>(null);
  const [playing, setPlaying] = useState(false);
  const [muted, setMuted] = useState(false);

  const start = edit?.start ?? 0;
  const end = edit?.end ?? 0;
  const { vertical, mode, blurFill, cropStyle, bgStyle, fgWrapStyle, innerStyle } = clipVisualStyle(edit?.look, edit?.color);
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
      src={`/media/${projectId}/source`}
      style={innerStyle}
      preload="metadata"
      muted={muted}
      onPlay={() => setPlaying(true)}
      onPause={() => setPlaying(false)}
      onTimeUpdate={onTime}
      playsInline
    />
  );

  return (
    <div>
      <div style={cropStyle}>
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
      </div>

      <div
        className="glass"
        style={{ display: "flex", alignItems: "center", gap: "var(--sp-3)", padding: "8px 12px", marginTop: "var(--sp-3)" }}
      >
        <button className="btn ghost" style={{ padding: "6px 12px" }} onClick={toggle}>
          {playing ? "Pause" : "Play"}
        </button>
        <button className="btn ghost" style={{ padding: "6px 10px" }} onClick={() => setMuted((m) => !m)}>
          {muted ? "Unmute" : "Mute"}
        </button>
        <span className="muted" style={{ fontSize: "var(--fs-xs)", fontVariantNumeric: "tabular-nums", marginLeft: "auto" }}>
          {fmtDuration(playhead - start)} / {fmtDuration(end - start)}
        </span>
      </div>
    </div>
  );
}
