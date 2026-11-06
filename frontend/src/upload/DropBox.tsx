import { Link } from "react-router-dom";
import { useUsage } from "@/api/usage";
import { fmtBytes } from "@/lib/format";
import { summarize } from "./settings";
import { useUploads, VIDEO_ACCEPT } from "./store";
import { useCanDrag } from "./useFileDrag";

const FALLBACK_MAX_MB = 2048;       // config.py quota_max_upload_bytes, until /usage has answered

const UPLOAD_ICON = "M12 16V4m0 0L8 8m4-4 4 4M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3";

/** The main page's upload box. It stays the same size in every state, so nothing below it moves. */
export function DropBox({ dragging, onOpenSettings }: { dragging: boolean; onOpenSettings: () => void }) {
  const { phase, pct, fileName, fileBytes, projectId, error, settings, start, cancel, dismiss } = useUploads();
  const { data: usage } = useUsage();
  const canDrag = useCanDrag();
  const maxBytes = (usage?.max_upload_mb ?? FALLBACK_MAX_MB) * 1024 ** 2;
  const maxText = fmtBytes(maxBytes).replace(".0", "");

  const state = dragging ? "over" : phase;
  return (
    <section className={`dropbox is-${state}`} aria-label="Upload a video">
      <div className="dropbox-area">
        {state === "over" && (
          <>
            <span className="dropbox-icon on" aria-hidden><Icon d={UPLOAD_ICON} /></span>
            <p className="dropbox-title">Let go to upload</p>
            <p className="dropbox-sub">It starts right away, with the settings below.</p>
          </>
        )}

        {state === "idle" && (
          <>
            <span className="dropbox-icon" aria-hidden><Icon d={UPLOAD_ICON} /></span>
            <h1 className="dropbox-title">{canDrag ? "Drop your video here" : "Choose a video"}</h1>
            <p className="dropbox-sub">
              {canDrag ? `MP4, MOV, MKV, WEBM, AVI, M4V, M2TS or MTS, up to ${maxText}.` : `MP4, MOV, MKV, WEBM and more, up to ${maxText}.`}
            </p>
            <label htmlFor="video-file" className="btn dropbox-choose">{canDrag ? "Or choose a file" : "Choose video"}</label>
            <input
              id="video-file"
              className="visually-hidden"
              type="file"
              accept={VIDEO_ACCEPT}
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) void start(file, { maxBytes });
                e.target.value = "";
              }}
            />
            <p className="dropbox-note">Kappacut works with English-language videos for now. Only upload videos you own or have permission to use.</p>
            {error && <p className="dropbox-error" role="alert">{error}</p>}
          </>
        )}

        {state === "uploading" && (
          <div className="dropbox-progress" role="status">
            <p className="dropbox-file">{fileName}</p>
            <p className="dropbox-sub">{pct}%  ·  {fmtBytes((fileBytes * pct) / 100)} of {fmtBytes(fileBytes)}</p>
            <div className="meter live" role="progressbar" aria-label="Upload progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}>
              <span style={{ width: `${pct}%` }} />
            </div>
            <p className="dropbox-note">We'll start analyzing as soon as it's uploaded, with the settings below.</p>
            <button className="btn ghost compact" onClick={cancel}>Cancel upload</button>
          </div>
        )}

        {state === "done" && (
          <div className="dropbox-progress" role="status">
            <span className="dropbox-icon" aria-hidden><Icon d="m5 12 5 5 9-10" color="var(--good)" width={2} /></span>
            <p className="dropbox-title">Uploaded</p>
            <p className="dropbox-sub">We're analyzing it now. It's in your projects below.</p>
            <div className="dropbox-actions">
              {projectId && <Link className="btn" to={`/app/p/${projectId}`} onClick={dismiss}>Open project</Link>}
              <button className="btn ghost" onClick={dismiss}>Upload another</button>
            </div>
          </div>
        )}
      </div>

      <div className="dropbox-settings">
        <div className="dropbox-summary">
          <span className="eyebrow">Clip settings</span>
          <span>{summarize(settings, !canDrag)}</span>
        </div>
        {phase !== "uploading" && <button className="link-btn" onClick={onOpenSettings}>Change</button>}
      </div>
    </section>
  );
}

function Icon({ d, color = "currentColor", width = 1.75 }: { d: string; color?: string; width?: number }) {
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={width} strokeLinecap="round" strokeLinejoin="round">
      <path d={d} />
    </svg>
  );
}
