import { useRef, useState } from "react";
import { useEditor } from "./store";

const ZOOM_MAX = 2.2; // same ceiling as the Zoom slider

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));

/**
 * Drag-to-frame over a still of the source: the violet box is exactly the window
 * cut_clip crops — (1/zoom) of the frame, a 9:16 slice for "crop" and a 16:9 one for
 * "cropfill", placed at x=(iw-w)*px, y=(ih-h)*py. Drag the box to set px/py; pull its
 * corner to set zoom. The sliders below stay as the keyboard-accessible equivalent.
 */
export function SourceFramer() {
  const projectId = useEditor((s) => s.projectId);
  const activeIdx = useEditor((s) => s.activeIdx);
  const clip = useEditor((s) => s.clips[s.activeIdx]);
  const e = useEditor((s) => s.edits[s.activeIdx]);
  const patch = useEditor((s) => s.patch);
  const boxRef = useRef<HTMLDivElement>(null);
  const [srcAR, setSrcAR] = useState(16 / 9); // the still's real width/height once loaded

  if (!e || !clip) return null;
  const { mode, px, py, zoom } = e.look;
  // window size as a fraction of the source frame (width relative to iw, height to ih)
  const windowAR = mode === "crop" ? 9 / 16 : 16 / 9;
  const w1 = Math.min(1, windowAR / srcAR); // width at zoom 1
  const wN = w1 / zoom;
  const hN = 1 / zoom;
  const xN = (1 - wN) * px;
  const yN = (1 - hN) * py;

  function drag(kind: "move" | "zoom", ev: React.PointerEvent) {
    ev.preventDefault();
    ev.stopPropagation();
    const r = boxRef.current?.getBoundingClientRect();
    if (!r) return;
    (ev.target as HTMLElement).setPointerCapture(ev.pointerId);
    const x0 = ev.clientX, y0 = ev.clientY, px0 = px, py0 = py;
    const move = (m: PointerEvent) => {
      if (kind === "move") {
        const dx = (m.clientX - x0) / r.width;
        const dy = (m.clientY - y0) / r.height;
        patch(activeIdx, (d) => {
          d.look.follow = false;                          // a hand-placed frame stops following
          if (1 - wN > 1e-3) d.look.px = +clamp(px0 + dx / (1 - wN), 0, 1).toFixed(3);
          if (1 - hN > 1e-3) d.look.py = +clamp(py0 + dy / (1 - hN), 0, 1).toFixed(3);
        });
      } else {
        // the corner follows the pointer: new width = pointer − window's left edge
        const newW = (m.clientX - r.left) / r.width - xN;
        patch(activeIdx, (d) => {
          d.look.zoom = +clamp(w1 / Math.max(newW, 1e-3), 1, ZOOM_MAX).toFixed(2);
        });
      }
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }

  return (
    <div>
      <div
        ref={boxRef}
        className="framer"
        style={{ aspectRatio: `${srcAR}` }}
      >
        <img
          src={`/media/${projectId}/frame.jpg?t=${Math.floor(clip.start_seconds)}`}
          alt=""
          draggable={false}
          onLoad={(ev) => {
            const im = ev.currentTarget;
            if (im.naturalWidth && im.naturalHeight) setSrcAR(im.naturalWidth / im.naturalHeight);
          }}
        />
        {clip.face && (
          <span
            className="framer-face"
            title={`Face seen in ${Math.round(clip.face.seen * 100)}% of sampled frames`}
            style={{ left: `${clip.face.x * 100}%`, top: `${clip.face.y * 100}%` }}
          />
        )}
        <div
          className="framer-window"
          role="img"
          aria-label={`Framing window: zoom ${zoom.toFixed(2)}×, horizontal ${Math.round(px * 100)}%, vertical ${Math.round(py * 100)}%`}
          onPointerDown={(ev) => drag("move", ev)}
          style={{ left: `${xN * 100}%`, top: `${yN * 100}%`, width: `${wN * 100}%`, height: `${hN * 100}%` }}
        >
          <span className="framer-handle" onPointerDown={(ev) => drag("zoom", ev)} title="Drag to zoom" />
        </div>
      </div>
      <p className="muted" style={{ fontSize: "var(--fs-2xs)", margin: "6px 0 0" }}>
        {e.look.follow
          ? "Following the face — play the clip to see the crop pan · drag the box to place it by hand instead"
          : "Drag the box to frame the subject · pull its corner to zoom"}
      </p>
    </div>
  );
}
