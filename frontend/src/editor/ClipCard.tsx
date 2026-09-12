import { useEditor } from "./store";
import { clipVisualStyle, clipLoopHandlers } from "./clipVisual";
import { fmtDuration } from "@/lib/format";

export function ClipCard({ index, onEdit }: { index: number; onEdit: (i: number) => void }) {
  const projectId = useEditor((s) => s.projectId);
  const clip = useEditor((s) => s.clips[index]);
  const edit = useEditor((s) => s.edits[index]);
  const toggleKeep = useEditor((s) => s.toggleKeep);

  if (!clip || !edit) return null;

  const start = edit.start;
  const end = edit.end;
  const { vertical, mode, blurFill, cropStyle, bgStyle, fgWrapStyle, innerStyle } = clipVisualStyle(edit.look, edit.color);
  const loop = clipLoopHandlers(start, end);

  const video = (
    <video
      src={`/media/${projectId}/source`}
      style={innerStyle}
      preload="metadata"
      controls
      muted
      playsInline
      {...loop}
    />
  );

  return (
    <div className="glass" style={{ padding: "var(--sp-3)", opacity: edit.keep ? 1 : 0.45 }}>
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
            {...loop}
          />
        )}
        {mode === "cropfill" && vertical ? <div style={fgWrapStyle}>{video}</div> : video}
      </div>

      <div style={{ marginTop: "var(--sp-3)", minHeight: 40 }}>
        <div style={{ fontFamily: "'Space Grotesk'", fontWeight: 600, fontSize: "var(--fs-sm)" }}>
          {clip.title || clip.hook || "Moment"}
        </div>
        <div className="muted" style={{ fontSize: "var(--fs-2xs)", marginTop: 2 }}>
          {fmtDuration(end - start)} · score {clip.score.toFixed(1)}
        </div>
      </div>

      <div style={{ display: "flex", gap: "var(--sp-2)", marginTop: "var(--sp-3)" }}>
        <button className="btn ghost" style={{ flex: 1, padding: "6px 0", fontSize: "var(--fs-xs)" }} onClick={() => toggleKeep(index)}>
          {edit.keep ? "Keep" : "Skip"}
        </button>
        <button className="btn" style={{ flex: 1, padding: "6px 0", fontSize: "var(--fs-xs)" }} onClick={() => onEdit(index)}>
          Edit
        </button>
      </div>
    </div>
  );
}
