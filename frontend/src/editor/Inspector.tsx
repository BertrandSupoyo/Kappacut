import { useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { useToast } from "@/components/Toast";
import { useEditor } from "./store";
import { Row, Seg, Slider, Toggle } from "./controls";
import { SourceFramer } from "./SourceFramer";
import {
  CAPTION_ANIMS,
  CAPTION_POSITIONS,
  CAPTION_STYLES,
  COLOR_PRESETS,
  CROP_MODES,
  faceFrame,
  type CropMode,
} from "./types";
import type { SfxItem } from "@/api/types";
import { fmtDuration } from "@/lib/format";

type Tab = "captions" | "reframe" | "colour" | "sound";
const TABS: { id: Tab; label: string }[] = [
  { id: "captions", label: "Captions" },
  { id: "reframe", label: "Reframe" },
  { id: "colour", label: "Colour" },
  { id: "sound", label: "Sound" },
];

export function Inspector({ sfx }: { sfx: SfxItem[] }) {
  const [tab, setTab] = useState<Tab>("captions");
  const i = useEditor((s) => s.activeIdx);
  const e = useEditor((s) => s.edits[s.activeIdx]);
  const clip = useEditor((s) => s.clips[s.activeIdx]);
  const patch = useEditor((s) => s.patch);
  if (!e) return null;

  return (
    <section className="glass inspector" aria-label="Clip settings">
      <div className="tabs" role="tablist" aria-label="Settings">
        {TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            id={`tab-${t.id}`}
            aria-selected={tab === t.id}
            aria-controls={`panel-${t.id}`}
            className={`tab${tab === t.id ? " on" : ""}`}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`}>
        {tab === "captions" && (
          <>
            <Toggle on={e.caption.on} onChange={(v) => patch(i, (d) => { d.caption.on = v; })} label="Burn captions in" />
            <fieldset disabled={!e.caption.on} className="dim-when-disabled" style={{ marginTop: "var(--sp-3)" }}>
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
            </fieldset>
          </>
        )}

        {tab === "reframe" && (
          <>
            <Toggle on={e.look.vertical} onChange={(v) => patch(i, (d) => { d.look.vertical = v; })} label="Reframe to 9:16 vertical" />
            <fieldset disabled={!e.look.vertical} className="dim-when-disabled" style={{ marginTop: "var(--sp-3)" }}>
              <Row label="Mode">
                <div className="mode-cards">
                  {CROP_MODES.map((m) => (
                    <button
                      key={m.id}
                      className={`mode-card${e.look.mode === m.id ? " on" : ""}`}
                      aria-pressed={e.look.mode === m.id}
                      onClick={() =>
                        patch(i, (d) => {
                          if (d.look.mode === m.id) return;
                          d.look.mode = m.id;
                          // start each mode on the detected face (else the centre): a leftover
                          // Horizontal of 100% is harmless in Subject + blur at zoom 1 but in
                          // Crop it shoves the 9:16 window to the far edge, off the person
                          d.look.zoom = 1;
                          Object.assign(d.look, faceFrame(clip?.face, m.id, 1));
                        })
                      }
                    >
                      <ModeGlyph mode={m.id} />
                      <span>{m.label}</span>
                    </button>
                  ))}
                </div>
              </Row>

              {e.look.mode !== "fill" && (
                <>
                  <Row label="Framing">
                    {clip?.face?.track && (
                      <Toggle
                        on={!!e.look.follow}
                        onChange={(v) => patch(i, (d) => { d.look.follow = v; })}
                        label="Follow face — the crop pans with the person"
                      />
                    )}
                    <SourceFramer />
                    {clip?.face && (
                      <button
                        className="btn ghost"
                        style={{ padding: "6px 12px", alignSelf: "flex-start" }}
                        onClick={() => patch(i, (d) => {
                          Object.assign(d.look, faceFrame(clip.face, d.look.mode, d.look.zoom));
                          d.look.follow = false;           // a fixed frame on the face's usual spot
                        })}
                      >
                        Centre on face
                      </button>
                    )}
                  </Row>
                  <Row label={`Zoom ${e.look.zoom.toFixed(2)}×`}>
                    <Slider value={e.look.zoom} min={1} max={2.2} step={0.05} onChange={(v) => patch(i, (d) => { d.look.zoom = v; })} fmt={(v) => `${v.toFixed(2)}×`} />
                  </Row>
                  <div className="two-col">
                    <Row label="Horizontal">
                      <Slider value={e.look.px} min={0} max={1} step={0.02} onChange={(v) => patch(i, (d) => { d.look.px = v; d.look.follow = false; })} fmt={(v) => `${Math.round(v * 100)}%`} />
                    </Row>
                    <Row label="Vertical">
                      <Slider value={e.look.py} min={0} max={1} step={0.02} onChange={(v) => patch(i, (d) => { d.look.py = v; d.look.follow = false; })} fmt={(v) => `${Math.round(v * 100)}%`} />
                    </Row>
                  </div>
                </>
              )}
              {e.look.mode !== "crop" && (
                <Row label={`Background blur ${e.look.blur}`}>
                  <Slider value={e.look.blur} min={0} max={60} step={2} onChange={(v) => patch(i, (d) => { d.look.blur = v; })} />
                </Row>
              )}
            </fieldset>
          </>
        )}

        {tab === "colour" && (
          <>
            <Row label="Preset">
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
            <button
              className="btn ghost"
              style={{ padding: "6px 12px" }}
              onClick={() => patch(i, (d) => { d.color = { preset: "none", b: 0, c: 1, s: 1 }; })}
            >
              Reset colour
            </button>
          </>
        )}

        {tab === "sound" && <SfxPane sfx={sfx} />}
      </div>
    </section>
  );
}

/** A 9:16 thumbnail of what each reframe mode does to a 16:9 source. */
function ModeGlyph({ mode }: { mode: CropMode }) {
  return (
    <span aria-hidden className={`mode-glyph ${mode}`}>
      {mode !== "crop" && <span className="mode-glyph-fg" />}
    </span>
  );
}

const SFX_ACCEPT = ".mp3,.wav,.m4a,.ogg,.aac,.flac";   // app/sfx.py SFX_EXTS
const SFX_MAX_MB = 8;                                  // app/sfx.py SFX_MAX_BYTES

function SfxPane({ sfx }: { sfx: SfxItem[] }) {
  const i = useEditor((s) => s.activeIdx);
  const e = useEditor((s) => s.edits[s.activeIdx]);
  const playhead = useEditor((s) => s.playhead);
  const patch = useEditor((s) => s.patch);
  const addSfx = useEditor((s) => s.addSfx);
  const removeSfx = useEditor((s) => s.removeSfx);
  const qc = useQueryClient();
  const toast = useToast();
  const fileInput = useRef<HTMLInputElement>(null);
  const audio = useRef<HTMLAudioElement | null>(null);
  const [playing, setPlaying] = useState<string | null>(null);
  const upload = useMutation({
    mutationFn: (file: File) => {
      const body = new FormData();
      body.append("file", file);
      return api<SfxItem>("/api/sfx", { method: "POST", body });
    },
    onSuccess: (item) => {
      qc.invalidateQueries({ queryKey: ["sfx"] });
      toast(`Added “${item.name}” to your sounds`);
    },
    onError: (err) => toast((err as Error).message || "Couldn't upload that sound.", "error"),
  });
  const remove = useMutation({
    mutationFn: (s: SfxItem) => api(`/api/sfx/${s.id}`, { method: "DELETE" }),
    onSuccess: (_r, s) => {
      qc.invalidateQueries({ queryKey: ["sfx"] });
      toast(`Deleted “${s.name}”`);
    },
    onError: (err) => toast((err as Error).message || "Couldn't delete that sound.", "error"),
  });
  if (!e) return null;
  const localAt = Math.max(0, +(playhead - e.start).toFixed(1));

  function preview(s: SfxItem) {
    audio.current?.pause();
    if (playing === s.id) {
      setPlaying(null);
      return;
    }
    const a = new Audio(s.url);                       // /media/sfx/<id> redirects to a signed URL
    a.onended = () => setPlaying(null);
    a.play().catch(() => setPlaying(null));
    audio.current = a;
    setPlaying(s.id);
  }

  function pick(f: File | undefined) {
    if (!f) return;
    if (f.size > SFX_MAX_MB * 1024 * 1024) {
      toast(`That file is ${(f.size / 1024 / 1024).toFixed(1)} MB — sounds can be up to ${SFX_MAX_MB} MB.`, "error");
      return;
    }
    upload.mutate(f);
  }

  const mine = sfx.filter((s) => !s.builtin);
  const builtin = sfx.filter((s) => s.builtin);
  const chips = (list: SfxItem[]) => (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
      {list.map((s) => (
        <span key={s.id} className="sfx-chip">
          <button
            className="sfx-play"
            aria-label={playing === s.id ? `Stop ${s.name}` : `Preview ${s.name}`}
            onClick={() => preview(s)}
          >
            {playing === s.id ? "■" : "▶"}
          </button>
          <button className="sfx-add" onClick={() => addSfx(i, { file: s.file, name: s.name, at: localAt, gain: -3 })}>
            + {s.name}
          </button>
          {!s.builtin && (
            <button
              className="sfx-del"
              aria-label={`Delete ${s.name}`}
              title="Delete this sound"
              disabled={remove.isPending}
              onClick={() => confirm(`Delete “${s.name}” from your sounds? Clips already using it will render without it.`) && remove.mutate(s)}
            >
              ×
            </button>
          )}
        </span>
      ))}
    </div>
  );

  return (
    <>
      <Toggle on={e.sfxAuto} onChange={(v) => patch(i, (d) => { d.sfxAuto = v; })} label="Whoosh on the clip's entrance" />
      <div style={{ marginTop: "var(--sp-3)" }}>
        <Row label={`Add at playhead (${fmtDuration(localAt)})`}>
          {chips(builtin)}
        </Row>
        <Row label="Your sounds">
          {mine.length > 0 ? chips(mine) : <span className="muted" style={{ fontSize: "var(--fs-xs)" }}>None yet.</span>}
          <input
            ref={fileInput}
            type="file"
            accept={SFX_ACCEPT}
            hidden
            onChange={(ev) => {
              pick(ev.target.files?.[0]);
              ev.target.value = "";
            }}
          />
          <button
            className="btn ghost"
            style={{ padding: "6px 12px", alignSelf: "flex-start" }}
            disabled={upload.isPending}
            onClick={() => fileInput.current?.click()}
          >
            {upload.isPending ? "Uploading…" : "Upload sound"}
          </button>
          <span className="muted" style={{ fontSize: "var(--fs-2xs)" }}>MP3, WAV, M4A, OGG, AAC or FLAC · up to {SFX_MAX_MB} MB</span>
        </Row>
      </div>
      {e.sfx.length > 0 && (
        <Row label="Placed">
          <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 4 }}>
            {e.sfx
              .slice()
              .sort((a, b) => a.at - b.at)
              .map((s, k) => (
                <li key={k} style={{ display: "flex", alignItems: "center", gap: "var(--sp-2)", fontSize: "var(--fs-xs)" }}>
                  <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis" }}>{s.name}</span>
                  <span className="muted">@ {fmtDuration(s.at)}</span>
                  <button
                    className="btn danger"
                    style={{ padding: "1px 9px", fontSize: "var(--fs-2xs)" }}
                    aria-label={`Remove ${s.name} at ${fmtDuration(s.at)}`}
                    onClick={() => removeSfx(i, s.at, s.file)}
                  >
                    ×
                  </button>
                </li>
              ))}
          </ul>
        </Row>
      )}
    </>
  );
}
