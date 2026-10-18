import { useEditor } from "./store";
import { fmtDuration } from "@/lib/format";

/** Left rail: every clip with a still, its length and a keep toggle. Becomes a
 * horizontal strip below the wide breakpoint (see .ed-rail in global.css). */
export function ClipRail() {
  const projectId = useEditor((s) => s.projectId);
  const clips = useEditor((s) => s.clips);
  const edits = useEditor((s) => s.edits);
  const activeIdx = useEditor((s) => s.activeIdx);
  const setActive = useEditor((s) => s.setActive);
  const toggleKeep = useEditor((s) => s.toggleKeep);
  const kept = clips.filter((_, i) => edits[i]?.keep !== false).length;

  return (
    <aside className="ed-rail" aria-label="Clips">
      <div className="ed-rail-head">
        <span className="eyebrow">Clips</span>
        <span className="muted" style={{ fontSize: "var(--fs-xs)" }}>{kept} of {clips.length} kept</span>
      </div>
      <ol className="ed-rail-list">
        {clips.map((c, i) => {
          const e = edits[i];
          const on = i === activeIdx;
          const keep = e?.keep !== false;
          return (
            <li key={i} className={`rail-item${on ? " on" : ""}${keep ? "" : " skipped"}`}>
              <button className="rail-pick" onClick={() => setActive(i)} aria-current={on ? "true" : undefined}>
                <img
                  className="rail-thumb"
                  // the analysis's own start, not the trimmed one: every distinct second is
                  // an ffmpeg run + stored object server-side, so don't refetch while dragging
                  src={`/media/${projectId}/frame.jpg?t=${Math.floor(c.start_seconds)}`}
                  alt=""
                  loading="lazy"
                />
                <span className="rail-text">
                  <span className="rail-title">{i + 1}. {c.title || c.hook || "Moment"}</span>
                  <span className="muted" style={{ fontSize: "var(--fs-2xs)" }}>
                    {fmtDuration((e?.end ?? c.end_seconds) - (e?.start ?? c.start_seconds))} ·{" "}
                    {c.virality !== undefined ? `${c.virality}/100` : `score ${c.score.toFixed(1)}`}
                  </span>
                </span>
              </button>
              <button
                className={`pill${keep ? " on" : ""}`}
                onClick={() => toggleKeep(i)}
                aria-pressed={keep}
                title={keep ? "In the render — click to skip" : "Left out of the render — click to keep"}
              >
                {keep ? "Kept" : "Skipped"}
              </button>
            </li>
          );
        })}
      </ol>
    </aside>
  );
}

/** The active clip's title, timing, the analysis's reason, and its AI-edit / keep toggles. */
export function StageHeader() {
  const clips = useEditor((s) => s.clips);
  const activeIdx = useEditor((s) => s.activeIdx);
  const e = useEditor((s) => s.edits[s.activeIdx]);
  const toggleKeep = useEditor((s) => s.toggleKeep);
  const toggleAuto = useEditor((s) => s.toggleAuto);
  const c = clips[activeIdx];
  if (!c || !e) return null;

  return (
    <div className="stage-head">
      <div style={{ minWidth: 0, flex: "1 1 220px" }}>
        <div style={{ fontFamily: "'Space Grotesk'", fontWeight: 600 }}>{activeIdx + 1}. {c.title || c.hook || "Moment"}</div>
        <div className="muted" style={{ fontSize: "var(--fs-xs)", marginTop: 2 }}>
          {fmtDuration(e.start)} – {fmtDuration(e.end)} · {fmtDuration(e.end - e.start)}
          {c.virality === undefined && ` · score ${c.score.toFixed(1)}`}
          {c.beat_type ? ` · ${c.beat_type}` : ""}
        </div>
        {c.why && <p className="muted" style={{ fontSize: "var(--fs-xs)", margin: "var(--sp-2) 0 0" }}>{c.why}</p>}
        {c.breakdown && (
          <div className="score-row" aria-label={`Score ${c.virality} out of 100`}>
            {(["hook", "story", "energy", "clarity", "pacing"] as const).map((k) => (
              <div key={k} className="score-part" title={`${k}: ${Math.round(c.breakdown![k] * 100)}/100`}>
                <span style={{ textTransform: "capitalize" }}>{k}</span>
                <div className="score-bar"><i style={{ width: `${Math.round(c.breakdown![k] * 100)}%` }} /></div>
              </div>
            ))}
          </div>
        )}
      </div>
      {c.virality !== undefined && (
        <div style={{ textAlign: "right" }}>
          <div className="score-total">{c.virality}</div>
          <div className="muted" style={{ fontSize: "var(--fs-2xs)" }}>/ 100</div>
        </div>
      )}
      <div style={{ display: "flex", gap: "var(--sp-2)", flexWrap: "wrap" }}>
        {c.auto && (
          <button
            className={`pill${e.autoOff ? "" : " on"}`}
            onClick={() => toggleAuto(activeIdx)}
            aria-pressed={!e.autoOff}
            title="Reframe, captions, colour and SFX suggested by the analysis"
          >
            {e.autoOff ? "AI edit off" : "AI edit on"}
          </button>
        )}
        <button
          className={`pill${e.keep ? " on" : ""}`}
          onClick={() => toggleKeep(activeIdx)}
          aria-pressed={e.keep}
          title={e.keep ? "In the render — click to skip" : "Left out of the render — click to keep"}
        >
          {e.keep ? "✓ Kept" : "Skipped"}
        </button>
      </div>
    </div>
  );
}
