import { useCallback, useRef } from "react";
import { useEditor } from "./store";
import { fmtDuration } from "@/lib/format";

export function Timeline() {
  const projectId = useEditor((s) => s.projectId);
  const duration = useEditor((s) => s.duration);
  const clips = useEditor((s) => s.clips);
  const activeIdx = useEditor((s) => s.activeIdx);
  const edit = useEditor((s) => s.edits[s.activeIdx]);
  const snaps = useEditor((s) => s.snaps);
  const playhead = useEditor((s) => s.playhead);
  const setActive = useEditor((s) => s.setActive);
  const setPlayhead = useEditor((s) => s.setPlayhead);
  const patch = useEditor((s) => s.patch);

  const barRef = useRef<HTMLDivElement>(null);
  const pct = (t: number) => (duration ? (t / duration) * 100 : 0);

  const timeAt = useCallback(
    (clientX: number) => {
      const r = barRef.current?.getBoundingClientRect();
      if (!r) return 0;
      return Math.max(0, Math.min(duration, ((clientX - r.left) / r.width) * duration));
    },
    [duration],
  );

  const snap = useCallback(
    (t: number) => {
      const tol = duration * 0.006;
      let best = t;
      let bd = tol;
      for (const s of snaps) {
        const d = Math.abs(s - t);
        if (d < bd) {
          bd = d;
          best = s;
        }
      }
      return best;
    },
    [snaps, duration],
  );

  function dragHandle(which: "start" | "end", e: React.PointerEvent) {
    e.preventDefault();
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
    const move = (ev: PointerEvent) => {
      const t = snap(timeAt(ev.clientX));
      patch(activeIdx, (d) => {
        if (which === "start") d.start = Math.min(t, d.end - 1);
        else d.end = Math.max(t, d.start + 1);
      });
      setPlayhead(t);
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }

  if (!edit) return null;

  return (
    <div style={{ marginTop: "var(--sp-4)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: "var(--fs-2xs)", color: "var(--text-mut)", marginBottom: 4 }}>
        <span>{fmtDuration(edit.start)} – {fmtDuration(edit.end)}</span>
        <span>clip {fmtDuration(edit.end - edit.start)} · source {fmtDuration(duration)}</span>
      </div>

      <div
        ref={barRef}
        onPointerDown={(e) => {
          if ((e.target as HTMLElement).dataset.handle) return;
          setPlayhead(timeAt(e.clientX));
        }}
        style={{
          position: "relative",
          height: 54,
          borderRadius: "var(--r-sm)",
          background: `#0a0910 url(/media/${projectId}/waveform.png) center/100% 100% no-repeat`,
          border: "1px solid var(--border)",
          cursor: "text",
          userSelect: "none",
          touchAction: "none",
        }}
      >
        {/* other clips */}
        {clips.map((c, i) =>
          i === activeIdx ? null : (
            <div
              key={i}
              onPointerDown={(e) => {
                e.stopPropagation();
                setActive(i);
              }}
              title={c.title || c.hook}
              style={{
                position: "absolute",
                left: `${pct(c.start_seconds)}%`,
                width: `${pct(c.end_seconds - c.start_seconds)}%`,
                top: 0,
                bottom: 0,
                background: "rgba(255,255,255,.05)",
                borderLeft: "1px solid rgba(255,255,255,.12)",
                cursor: "pointer",
              }}
            />
          ),
        )}

        {/* active selection */}
        <div
          style={{
            position: "absolute",
            left: `${pct(edit.start)}%`,
            width: `${pct(edit.end - edit.start)}%`,
            top: 0,
            bottom: 0,
            background: "rgba(169,155,255,.18)",
            border: "1px solid var(--accent)",
            borderRadius: 3,
          }}
        />
        {(["start", "end"] as const).map((w) => (
          <div
            key={w}
            data-handle={w}
            onPointerDown={(e) => dragHandle(w, e)}
            style={{
              position: "absolute",
              left: `${pct(w === "start" ? edit.start : edit.end)}%`,
              top: -3,
              bottom: -3,
              width: 10,
              marginLeft: -5,
              background: "var(--accent)",
              borderRadius: 3,
              cursor: "ew-resize",
              touchAction: "none",
            }}
          />
        ))}

        {/* playhead */}
        <div
          style={{
            position: "absolute",
            left: `${pct(playhead)}%`,
            top: -4,
            bottom: -4,
            width: 2,
            marginLeft: -1,
            background: "var(--cyan)",
            pointerEvents: "none",
          }}
        />
      </div>
      <p className="muted" style={{ fontSize: "var(--fs-2xs)", marginTop: 6 }}>
        Drag the violet handles to trim · they snap to sentence boundaries · click the bar to scrub · click a grey block to switch clip
      </p>
    </div>
  );
}
