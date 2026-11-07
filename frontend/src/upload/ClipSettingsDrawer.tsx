import { useEffect, useRef } from "react";
import { Row, Seg, Toggle } from "@/editor/controls";
import { CONTENT_TYPES, COUNTS, LENGTHS, PLATFORMS, VIBES } from "./settings";
import { useUploads } from "./store";

/** Slides over the page instead of pushing it down. Every choice is optional, and the optional
 * ones can be turned off by choosing them again. */
export function ClipSettingsDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const settings = useUploads((s) => s.settings);
  const set = useUploads((s) => s.setSettings);
  const reset = useUploads((s) => s.resetSettings);
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (open) closeRef.current?.focus();
  }, [open]);

  if (!open) return null;
  return (
    <div className="drawer-wrap" onKeyDown={(e) => e.key === "Escape" && onClose()}>
      <button className="drawer-scrim" aria-label="Close clip settings" tabIndex={-1} onClick={onClose} />
      <aside className="drawer" role="dialog" aria-modal="true" aria-labelledby="clip-settings-title">
        <div className="drawer-head">
          <h2 id="clip-settings-title">Clip settings</h2>
          <button ref={closeRef} className="drawer-close" aria-label="Close" onClick={onClose}>
            <svg aria-hidden width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M6 6l12 12M18 6 6 18" /></svg>
          </button>
        </div>
        <p className="muted drawer-help">All optional. These apply to your next video.</p>

        <Row label="Content type">
          <Seg options={CONTENT_TYPES} value={settings.type} onChange={(v) => set({ type: v })} />
        </Row>
        <Row label="Platform">
          <Seg options={PLATFORMS} value={settings.platform} onChange={(v) => set({ platform: settings.platform === v ? "" : v })} />
        </Row>
        <Row label="Vibe">
          <Seg options={VIBES} value={settings.vibe} onChange={(v) => set({ vibe: settings.vibe === v ? "" : v })} />
        </Row>
        <Row label="Clip length">
          <Seg options={LENGTHS} value={settings.length} onChange={(v) => set({ length: settings.length === v ? "" : v })} />
        </Row>
        <Row label="How many clips">
          <Seg options={COUNTS} value={settings.count} onChange={(v) => set({ count: settings.count === v ? "" : v })} />
        </Row>
        <div className="field">
          <label htmlFor="clip-avoid">Anything to avoid</label>
          <input id="clip-avoid" placeholder="profanity, spoilers, small talk…" value={settings.avoid} onChange={(e) => set({ avoid: e.target.value })} />
        </div>
        <Row label="Review before rendering?">
          <Toggle
            on={settings.review}
            onChange={(v) => set({ review: v })}
            label={settings.review ? "On — I'll review clips in the editor first" : "Off — render automatically once picks are ready"}
          />
        </Row>

        <div className="drawer-actions">
          <button className="btn" onClick={onClose}>Done</button>
          <button className="btn ghost" onClick={reset}>Reset to Auto</button>
        </div>
      </aside>
    </div>
  );
}
