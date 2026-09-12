import { useState } from "react";
import { useEditor } from "./store";
import { Row, Seg, Slider, Toggle } from "./controls";
import {
  CAPTION_ANIMS,
  CAPTION_POSITIONS,
  CAPTION_STYLES,
  COLOR_PRESETS,
  CROP_MODES,
} from "./types";
import type { SfxItem } from "@/api/types";
import { fmtDuration } from "@/lib/format";

type Tab = "caption" | "look" | "sfx";

export function Inspector({ sfx }: { sfx: SfxItem[] }) {
  const [tab, setTab] = useState<Tab>("caption");
  const i = useEditor((s) => s.activeIdx);
  const e = useEditor((s) => s.edits[s.activeIdx]);
  const patch = useEditor((s) => s.patch);
  if (!e) return null;

  return (
    <div className="glass" style={{ padding: "var(--sp-4)" }}>
      <div style={{ display: "flex", gap: 4, marginBottom: "var(--sp-4)" }}>
        {(["caption", "look", "sfx"] as Tab[]).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            style={{
              flex: 1,
              padding: "6px 0",
              fontSize: "var(--fs-xs)",
              borderRadius: "var(--r-xs)",
              border: "1px solid var(--border)",
              background: tab === t ? "rgba(169,155,255,.16)" : "transparent",
              color: tab === t ? "var(--text)" : "var(--text-mut)",
              cursor: "pointer",
              textTransform: "capitalize",
            }}
          >
            {t === "look" ? "9:16" : t}
          </button>
        ))}
      </div>

      {tab === "caption" && (
        <>
          <Toggle on={e.caption.on} onChange={(v) => patch(i, (d) => { d.caption.on = v; })} label="Burn captions in" />
          <div style={{ opacity: e.caption.on ? 1 : 0.4, pointerEvents: e.caption.on ? "auto" : "none", marginTop: "var(--sp-3)" }}>
            <Row label="Style">
              <Seg options={CAPTION_STYLES} value={e.caption.style} onChange={(v) => patch(i, (d) => { d.caption.style = v; })} />
            </Row>
            <Row label="Position">
              <Seg options={CAPTION_POSITIONS} value={e.caption.position} onChange={(v) => patch(i, (d) => { d.caption.position = v; })} />
            </Row>
            <Row label="Animation">
              <Seg options={CAPTION_ANIMS} value={e.caption.anim} onChange={(v) => patch(i, (d) => { d.caption.anim = v; })} />
            </Row>
            <Row label={`Size ${e.caption.size.toFixed(2)}×`}>
              <Slider value={e.caption.size} min={0.7} max={1.6} step={0.05} onChange={(v) => patch(i, (d) => { d.caption.size = v; })} fmt={(v) => `${v.toFixed(2)}×`} />
            </Row>
            <Toggle on={e.caption.karaoke} onChange={(v) => patch(i, (d) => { d.caption.karaoke = v; })} label="Karaoke — highlight each word" />
          </div>
        </>
      )}

      {tab === "look" && (
        <>
          <Toggle on={e.look.vertical} onChange={(v) => patch(i, (d) => { d.look.vertical = v; })} label="Reframe to 9:16 vertical" />
          <div style={{ opacity: e.look.vertical ? 1 : 0.4, pointerEvents: e.look.vertical ? "auto" : "none", marginTop: "var(--sp-3)" }}>
            <Row label="Mode">
              <Seg options={CROP_MODES} value={e.look.mode} onChange={(v) => patch(i, (d) => { d.look.mode = v; })} />
            </Row>
            {e.look.mode !== "fill" && (
              <>
                <Row label={`Zoom ${e.look.zoom.toFixed(2)}×`}>
                  <Slider value={e.look.zoom} min={1} max={2.2} step={0.05} onChange={(v) => patch(i, (d) => { d.look.zoom = v; })} fmt={(v) => `${v.toFixed(2)}×`} />
                </Row>
                <Row label="Horizontal">
                  <Slider value={e.look.px} min={0} max={1} step={0.02} onChange={(v) => patch(i, (d) => { d.look.px = v; })} fmt={(v) => `${Math.round(v * 100)}%`} />
                </Row>
                <Row label="Vertical">
                  <Slider value={e.look.py} min={0} max={1} step={0.02} onChange={(v) => patch(i, (d) => { d.look.py = v; })} fmt={(v) => `${Math.round(v * 100)}%`} />
                </Row>
              </>
            )}
            {e.look.mode !== "crop" && (
              <Row label={`Blur ${e.look.blur}`}>
                <Slider value={e.look.blur} min={0} max={60} step={2} onChange={(v) => patch(i, (d) => { d.look.blur = v; })} />
              </Row>
            )}
          </div>

          <hr style={{ border: 0, borderTop: "1px solid var(--border)", margin: "var(--sp-4) 0" }} />
          <Row label="Colour preset">
            <Seg options={COLOR_PRESETS} value={e.color.preset} onChange={(v) => patch(i, (d) => { d.color.preset = v; })} />
          </Row>
          <Row label="Brightness">
            <Slider value={e.color.b} min={-0.3} max={0.3} step={0.02} onChange={(v) => patch(i, (d) => { d.color.b = v; })} fmt={(v) => v.toFixed(2)} />
          </Row>
          <Row label="Contrast">
            <Slider value={e.color.c} min={0.7} max={1.4} step={0.02} onChange={(v) => patch(i, (d) => { d.color.c = v; })} fmt={(v) => v.toFixed(2)} />
          </Row>
          <Row label="Saturation">
            <Slider value={e.color.s} min={0} max={1.6} step={0.05} onChange={(v) => patch(i, (d) => { d.color.s = v; })} fmt={(v) => v.toFixed(2)} />
          </Row>
        </>
      )}

      {tab === "sfx" && <SfxPane sfx={sfx} />}
    </div>
  );
}

function SfxPane({ sfx }: { sfx: SfxItem[] }) {
  const i = useEditor((s) => s.activeIdx);
  const e = useEditor((s) => s.edits[s.activeIdx]);
  const playhead = useEditor((s) => s.playhead);
  const patch = useEditor((s) => s.patch);
  const addSfx = useEditor((s) => s.addSfx);
  const removeSfx = useEditor((s) => s.removeSfx);
  if (!e) return null;
  const localAt = Math.max(0, +(playhead - e.start).toFixed(1));

  return (
    <>
      <Toggle on={e.sfxAuto} onChange={(v) => patch(i, (d) => { d.sfxAuto = v; })} label="Whoosh on the clip's entrance" />
      <Row label={`Add at playhead (${fmtDuration(localAt)})`}>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
          {sfx.map((s) => (
            <button
              key={s.id}
              onClick={() => addSfx(i, { file: s.file, name: s.name, at: localAt, gain: -3 })}
              style={{
                padding: "5px 10px",
                fontSize: "var(--fs-xs)",
                borderRadius: "var(--r-xs)",
                border: "1px solid var(--border)",
                background: "transparent",
                color: "var(--text-dim)",
                cursor: "pointer",
              }}
            >
              + {s.name}
            </button>
          ))}
        </div>
      </Row>
      {e.sfx.length > 0 && (
        <Row label="Placed">
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {e.sfx
              .slice()
              .sort((a, b) => a.at - b.at)
              .map((s, k) => (
                <div key={k} style={{ display: "flex", alignItems: "center", gap: "var(--sp-2)", fontSize: "var(--fs-xs)" }}>
                  <span style={{ flex: 1 }}>{s.name}</span>
                  <span className="muted">@ {fmtDuration(s.at)}</span>
                  <button className="btn danger" style={{ padding: "1px 7px", fontSize: "var(--fs-2xs)" }} onClick={() => removeSfx(i, s.at, s.file)}>
                    ×
                  </button>
                </div>
              ))}
          </div>
        </Row>
      )}
    </>
  );
}
