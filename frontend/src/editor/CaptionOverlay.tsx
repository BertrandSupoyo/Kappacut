import { useEffect, useMemo, useState, type CSSProperties, type RefObject } from "react";
import { useEditor } from "./store";
import { buildCues, CAP_STYLES, cueAt, type CaptionCue } from "./captions";
import type { ClipEdit } from "./types";

/** The clip's captions drawn over the preview video, as the render will burn them in.
 * Sits inside the frame from clipVisualStyle (a size container), so it scales with it.
 * The parent must remount it (a `key`) whenever it replaces the <video> element. */
export function CaptionOverlay({
  video,
  edit,
  vertical,
}: {
  video: RefObject<HTMLVideoElement | null>;
  edit: ClipEdit;
  vertical: boolean;
}) {
  const segments = useEditor((s) => s.segments);
  const { on, style, position, size, anim, karaoke } = edit.caption;
  const { start, end } = edit;
  const cues = useMemo(
    () => (on ? buildCues(segments, start, end, { style, karaoke }) : []),
    [on, segments, start, end, style, karaoke],
  );
  const [cue, setCue] = useState<CaptionCue | null>(null);

  useEffect(() => {
    const v = video.current;
    if (!v) return;
    let frame = 0;
    const sync = () => setCue(cueAt(cues, v.currentTime));
    // timeupdate only fires ~4x a second, too coarse for a karaoke highlight
    const tick = () => {
      sync();
      frame = requestAnimationFrame(tick);
    };
    const onPlay = () => {
      cancelAnimationFrame(frame);
      tick();
    };
    const onStop = () => {
      cancelAnimationFrame(frame);
      sync();
    };
    v.addEventListener("play", onPlay);
    v.addEventListener("pause", onStop);
    v.addEventListener("seeked", sync);
    v.addEventListener("timeupdate", sync);
    if (v.paused) sync();
    else tick();
    return () => {
      cancelAnimationFrame(frame);
      v.removeEventListener("play", onPlay);
      v.removeEventListener("pause", onStop);
      v.removeEventListener("seeked", sync);
      v.removeEventListener("timeupdate", sync);
    };
  }, [video, cues]);

  if (!cue) return null;

  const st = CAP_STYLES[style] ?? CAP_STYLES.clean;
  // the render replaces a karaoke line's animation with a soft fade (_anim_lead in pipeline.py)
  const entrance = cue.hot < 0 ? anim : anim === "none" ? "none" : "soft";
  const vars = {
    "--cap-frame": vertical ? 1080 : 1920,
    "--cap-size": st.size * (size || 1),
    "--cap-outline": st.outline,
    "--cap-stroke": st.stroke,
    "--cap-hot": st.hot,
    color: st.color,
  } as CSSProperties;

  return (
    <div className={`cap-layer cap-${position}${st.box ? " cap-box" : ""}`} style={vars} aria-hidden>
      {/* a new key restarts the entrance; karaoke keeps one key per group of words */}
      <div key={cue.key} className={`cap-line cap-anim-${entrance}`} dir="auto">
        <span className="cap-text">
          {cue.words.map((w, i) => (
            <span key={i}>
              {i > 0 && cue.spaced && " "}
              {i === cue.hot ? <b className="cap-hot">{w}</b> : w}
            </span>
          ))}
        </span>
      </div>
    </div>
  );
}
