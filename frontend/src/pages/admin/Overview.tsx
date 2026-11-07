import { Link } from "react-router-dom";
import { fmtAgo } from "@/lib/format";
import { StatTile } from "../AdminTraining";
import { useOverview, type AttentionItem, type Overview } from "./api";
import { DayChart } from "./DayChart";
import { fmtBytes, JobStatus, LoadError, Loading, PageHead, StaleNote } from "./ui";

const n = (v: number) => v.toLocaleString();

export function AdminOverview() {
  const query = useOverview();
  if (query.isPending) return <Loading />;
  if (!query.data) return <div className="admin-page"><PageHead title="Overview" /><LoadError query={query} what="the overview" /></div>;
  const o = query.data;
  const ended = o.jobs_7d.done + o.jobs_7d.failed;

  return (
    <div className="admin-page">
      <PageHead title="Overview" note="How the service is doing right now, and over the last two weeks. Days are in UTC." />
      <StaleNote show={query.isRefetchError} />

      <SystemStrip o={o} />
      <Attention items={o.attention} />

      <section aria-label="Key numbers" className="stat-row three">
        <StatTile
          label="Users"
          value={n(o.users.total)}
          sub={`${n(o.users.new_7d)} new in 7 days · ${n(o.users.unverified)} unconfirmed${o.users.suspended ? ` · ${n(o.users.suspended)} suspended` : ""}`}
        />
        <StatTile label="Active users" value={n(o.users.active_7d)} sub="started a job in the last 7 days" />
        <StatTile
          label="Failed jobs"
          value={ended ? `${Math.round((o.jobs_7d.failed / ended) * 100)}%` : "—"}
          sub={ended ? `${n(o.jobs_7d.failed)} of ${n(ended)} in the last 7 days` : "no jobs finished in the last 7 days"}
        />
        <StatTile label="Minutes analyzed" value={n(o.usage.minutes_month)} sub={`this month · ${n(o.usage.minutes_last_month)} last month`} />
        <StatTile label="Clips rendered" value={n(o.usage.clips_month)} sub={`this month · ${n(o.usage.clips_last_month)} last month`} />
        <StatTile label="Storage used" value={fmtBytes(o.storage_bytes)} sub={`across ${n(o.projects)} project${o.projects === 1 ? "" : "s"}`} />
      </section>

      <section aria-label="The last 14 days" className="dc-grid">
        <DayChart
          title="Jobs finished"
          note="Cancelled jobs and refused videos aren't counted."
          days={o.days}
          series={[
            { key: "done", label: "Done", color: "var(--viz-rest)" },
            { key: "failed", label: "Failed", color: "var(--danger)" },
          ]}
          empty="No jobs finished in the last 14 days."
        />
        <DayChart
          title="Minutes analyzed"
          note="What users were charged for."
          days={o.days}
          series={[{ key: "minutes", label: "Minutes", color: "var(--viz-one)" }]}
          digits={1}
          empty="Nothing analyzed in the last 14 days."
        />
        <DayChart
          title="New accounts"
          note="Includes ones not confirmed yet."
          days={o.days}
          series={[{ key: "signups", label: "Accounts", color: "var(--viz-one)" }]}
          empty="No new accounts in the last 14 days."
        />
      </section>

      <div className="admin-two">
        <section className="glass admin-card" aria-labelledby="h-live">
          <div className="admin-card-head">
            <h2 id="h-live">Working now</h2>
            <span className="muted">{n(o.queue.running)} running · {n(o.queue.queued)} queued</span>
            <Link to="/admin/jobs?status=active" className="muted">All</Link>
          </div>
          {o.live.length === 0 ? (
            <p className="muted admin-none">Nothing is running.</p>
          ) : (
            <ul className="admin-list">
              {o.live.map((j) => (
                <li key={j.id}>
                  <div className="admin-list-top">
                    <JobStatus outcome={j.outcome} />
                    <span className="cell-clip" style={{ flex: 1 }} title={j.project ?? undefined}>{j.kind} · {j.project ?? "—"}</span>
                    <span className="muted">{j.pct}%</span>
                  </div>
                  <div className="meter thin live"><span style={{ width: `${j.pct}%` }} /></div>
                  <div className="muted cell-clip" title={j.user}>{j.user}</div>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="glass admin-card" aria-labelledby="h-failures">
          <div className="admin-card-head">
            <h2 id="h-failures">Recent failures</h2>
            <Link to="/admin/jobs?status=failed" className="muted">All</Link>
          </div>
          {o.failures.length === 0 ? (
            <p className="muted admin-none">No failures.</p>
          ) : (
            <ul className="admin-list">
              {o.failures.map((j) => (
                <li key={j.id}>
                  <div className="admin-list-top">
                    <span className="cell-clip" style={{ flex: 1 }} title={j.project ?? undefined}>{j.kind} · {j.project ?? "—"}</span>
                    <span className="muted">{j.finished_at ? fmtAgo(j.finished_at) : ""}</span>
                  </div>
                  <div className="muted admin-clamp" title={j.detail ?? undefined}>{j.detail}</div>
                  <div className="muted cell-clip" title={j.user}>{j.user}</div>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}

function SystemStrip({ o }: { o: Overview }) {
  const s = o.system;
  const workers = s.workers === null ? "Unknown" : s.workers === 0 ? "None running" : `${s.workers} running`;
  const items: { name: string; ok: boolean | null; value: string }[] = [
    { name: "Database", ok: s.db, value: s.db ? "Connected" : "Not answering" },
    { name: "Job queue", ok: s.redis, value: s.redis ? "Connected" : "Not answering" },
    { name: "Workers", ok: s.workers === null ? null : s.workers > 0, value: workers },
    { name: "Server disk", ok: s.disk_ok, value: `${s.disk_free_pct}% free` },
  ];
  return (
    <section aria-label="System status" className="sys-strip">
      {items.map((i) => (
        <div key={i.name} className="sys-tile">
          <span className={`sys-mark ${i.ok === null ? "unknown" : i.ok ? "ok" : "bad"}`} aria-hidden>{i.ok === null ? "?" : i.ok ? "✓" : "✕"}</span>
          <div style={{ minWidth: 0 }}>
            <div className="stat-label">{i.name}</div>
            <div className="sys-value">{i.value}</div>
          </div>
        </div>
      ))}
    </section>
  );
}

const MARK: Record<AttentionItem["level"], string> = { critical: "✕", warning: "!", info: "i" };

function Attention({ items }: { items: AttentionItem[] }) {
  if (!items.length) {
    return <p className="attn-clear"><span className="sys-mark ok" aria-hidden>✓</span>Nothing needs your attention.</p>;
  }
  return (
    <section className="glass admin-card" aria-labelledby="h-attention">
      <h2 id="h-attention" className="attn-title">Needs attention</h2>
      <ul className="attn-list">
        {items.map((item) => (
          <li key={item.text}>
            <span className={`attn-mark ${item.level}`} aria-label={item.level}>{MARK[item.level]}</span>
            <span className="attn-text">{item.text}</span>
            {item.to && <Link to={item.to} className="attn-go">Open</Link>}
          </li>
        ))}
      </ul>
    </section>
  );
}
