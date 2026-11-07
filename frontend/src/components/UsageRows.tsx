import type { Usage } from "@/api/types";
import { fmtBytes } from "@/lib/format";

/** Usage against a limit. The fill takes on a status colour as it nears the limit, and the numbers
 * beside it always say the same thing, so colour is never the only signal. */
export function UsageMeter({ used, cap }: { used: number; cap: number }) {
  const share = cap > 0 ? used / cap : 0;
  const color = share >= 1 ? "var(--danger)" : share >= 0.8 ? "var(--amber)" : "var(--text-mut)";
  return (
    <div className="meter thin" aria-hidden>
      <span style={{ width: `${Math.min(100, share * 100)}%`, background: color }} />
    </div>
  );
}

const mb = (v: number) => fmtBytes(v * 1024 ** 2).replace(".0 GB", " GB");
const num = (v: number) => (Math.round(v * 10) / 10).toLocaleString();

/** The limits a person is working against. Pick which with `show`. */
export function UsageRows({ usage, show }: { usage: Usage; show: ("minutes" | "storage" | "clips" | "projects")[] }) {
  const rows = {
    minutes: { label: "Minutes this month", used: usage.minutes.used, cap: usage.minutes.cap, text: `${num(usage.minutes.used)} of ${num(usage.minutes.cap)}` },
    storage: { label: "Storage", used: usage.storage_mb.used, cap: usage.storage_mb.cap, text: `${mb(usage.storage_mb.used)} of ${mb(usage.storage_mb.cap)}` },
    clips: { label: "Clips today", used: usage.renders_today.used, cap: usage.renders_today.cap, text: `${usage.renders_today.used} of ${usage.renders_today.cap}` },
    projects: { label: "Projects", used: usage.projects.used, cap: usage.projects.cap, text: `${usage.projects.used} of ${usage.projects.cap}` },
  };
  return (
    <>
      {show.map((key) => {
        const r = rows[key];
        return (
          <div key={key} className="usage-row">
            <div className="usage-row-top">
              <span className="muted">{r.label}</span>
              <span>{usage.unlimited ? <span className="muted">No limit</span> : r.text}</span>
            </div>
            {!usage.unlimited && <UsageMeter used={r.used} cap={r.cap} />}
          </div>
        );
      })}
    </>
  );
}
