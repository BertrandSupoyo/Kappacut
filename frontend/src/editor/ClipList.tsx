import { useEditor } from "./store";
import { fmtDuration } from "@/lib/format";

export function ClipList() {
  const clips = useEditor((s) => s.clips);
  const edits = useEditor((s) => s.edits);
  const activeIdx = useEditor((s) => s.activeIdx);
  const setActive = useEditor((s) => s.setActive);
  const toggleKeep = useEditor((s) => s.toggleKeep);
  const toggleAuto = useEditor((s) => s.toggleAuto);

  const active = clips[activeIdx];
  const activeEdit = edits[activeIdx];

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: "var(--sp-2)", flexWrap: "wrap", marginBottom: "var(--sp-3)" }}>
        {clips.map((c, i) => {
          const e = edits[i];
          return (
            <button
              key={i}
              onClick={() => setActive(i)}
              className="glass"
              style={{
                padding: "5px 11px",
                fontSize: "var(--fs-xs)",
                border: i === activeIdx ? "1px solid var(--accent)" : "1px solid var(--border)",
                opacity: e?.keep === false ? 0.4 : 1,
                cursor: "pointer",
                background: i === activeIdx ? "rgba(169,155,255,.14)" : "var(--surface)",
              }}
            >
              {i + 1}. {(c.title || c.hook || "moment").slice(0, 24)}
            </button>
          );
        })}
      </div>

      {active && activeEdit && (
        <div className="glass" style={{ padding: "var(--sp-4)" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "var(--sp-3)" }}>
            <div style={{ minWidth: 0, flex: 1 }}>
              <div style={{ fontFamily: "'Space Grotesk'", fontWeight: 600 }}>{active.title || active.hook || "Moment"}</div>
              <div className="muted" style={{ fontSize: "var(--fs-xs)", marginTop: 2 }}>
                {fmtDuration(activeEdit.start)} · {fmtDuration(activeEdit.end - activeEdit.start)} · score {active.score.toFixed(1)}
                {active.beat_type ? ` · ${active.beat_type}` : ""}
              </div>
            </div>
            {active.auto && (
              <button
                className="btn ghost"
                style={{
                  padding: "5px 11px",
                  fontSize: "var(--fs-xs)",
                  borderColor: activeEdit.autoOff ? "var(--border)" : "var(--accent)",
                  color: activeEdit.autoOff ? "var(--text-mut)" : "var(--accent)",
                }}
                onClick={() => toggleAuto(activeIdx)}
                title="reframe, captions, colour & SFX suggested by the analysis"
              >
                {activeEdit.autoOff ? "AI edit off" : "✨ AI edit"}
              </button>
            )}
            <button
              className="btn ghost"
              style={{ padding: "5px 11px", fontSize: "var(--fs-xs)" }}
              onClick={() => toggleKeep(activeIdx)}
            >
              {activeEdit.keep ? "Keep" : "Skip"}
            </button>
          </div>
          {active.why && <p className="muted" style={{ fontSize: "var(--fs-xs)", margin: "var(--sp-2) 0 0" }}>{active.why}</p>}
        </div>
      )}
    </div>
  );
}
