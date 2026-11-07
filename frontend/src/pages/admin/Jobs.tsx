import { Fragment, useState } from "react";
import { Link } from "react-router-dom";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { jget, jpost } from "@/api/client";
import { useAuth } from "@/auth/AuthContext";
import { useToast } from "@/components/Toast";
import { fmtAgo, fmtDuration } from "@/lib/format";
import type { AdminJob, JobCounts } from "./api";
import { ConfirmButton, JobStatus, LoadError, Loading, PageHead, PAGE_SIZE, Pager, SearchBox, StaleNote, useListParams } from "./ui";

const STATUS_FILTERS: { value: keyof JobCounts; label: string }[] = [
  { value: "all", label: "All" },
  { value: "active", label: "Active" },
  { value: "failed", label: "Failed" },
  { value: "stopped", label: "Stopped" },
  { value: "done", label: "Done" },
];

function took(j: AdminJob): string {
  if (!j.finished_at) return "—";
  const from = j.started_at ?? j.created_at;
  return fmtDuration((new Date(j.finished_at).getTime() - new Date(from).getTime()) / 1000);
}

export function AdminJobs() {
  const { get, set, page, setPage } = useListParams({ status: "all", kind: "all", q: "" });
  const status = get("status"), kind = get("kind"), q = get("q");

  const query = useQuery({
    queryKey: ["admin", "jobs", status, kind, q, page],
    queryFn: () => {
      const params = new URLSearchParams({ status, kind, q, limit: String(PAGE_SIZE), offset: String((page - 1) * PAGE_SIZE) });
      return jget<{ jobs: AdminJob[]; counts: JobCounts; total: number }>(`/api/admin/jobs?${params}`);
    },
    placeholderData: keepPreviousData,
    // quick while something is moving, slow when there's nothing to watch
    refetchInterval: (q) => ((q.state.data?.counts.active ?? 0) > 0 ? 3_000 : 15_000),
  });
  const data = query.data;

  return (
    <div className="admin-page">
      <PageHead
        title="Jobs"
        note={<>Every analysis and render. <b>Failed</b> means something went wrong. <b>Stopped</b> means it ended on purpose: cancelled, refused, or its project was removed.</>}
      />
      <div className="admin-filters">
        <div className="seg-row" role="group" aria-label="Filter by status">
          {STATUS_FILTERS.map((f) => (
            <button key={f.value} className={`pill${status === f.value ? " on" : ""}`} aria-pressed={status === f.value} onClick={() => set({ status: f.value })}>
              {f.label}{data ? ` ${data.counts[f.value].toLocaleString()}` : ""}
            </button>
          ))}
        </div>
        <select className="admin-input admin-select" aria-label="Filter by kind" value={kind} onChange={(e) => set({ kind: e.target.value })}>
          <option value="all">Analyze and render</option>
          <option value="analyze">Analyze</option>
          <option value="render">Render</option>
        </select>
        <SearchBox value={q} onChange={(v) => set({ q: v })} label="Search jobs" placeholder="Email or project" />
      </div>

      <StaleNote show={query.isRefetchError && !!data} />
      {query.isPending ? (
        <Loading />
      ) : !data ? (
        <LoadError query={query} what="the jobs" />
      ) : data.jobs.length === 0 ? (
        <p className="muted admin-none">No jobs match this filter.</p>
      ) : (
        <section className="glass admin-card" style={{ opacity: query.isPlaceholderData ? 0.6 : 1 }} aria-label="Jobs">
          <div className="table-wrap">
            <table className="admin-table">
              <thead>
                <tr><th>Status</th><th>Kind</th><th>Project</th><th>User</th><th>Progress or result</th><th>Started</th><th>Took</th><th /></tr>
              </thead>
              <tbody>
                {data.jobs.map((j) => <JobRow key={j.id} j={j} />)}
              </tbody>
            </table>
          </div>
        </section>
      )}
      {data && <Pager total={data.total} page={page} onPage={setPage} />}
    </div>
  );
}

function JobRow({ j }: { j: AdminJob }) {
  const qc = useQueryClient();
  const toast = useToast();
  const { user } = useAuth();
  const [open, setOpen] = useState(false);
  const cancel = useMutation({
    mutationFn: () => jpost<{ status: string }>(`/api/admin/jobs/${j.id}/cancel`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin"] }),
    onError: (e) => toast((e as Error).message || "Couldn't cancel the job.", "error"),
  });
  const live = j.status === "queued" || j.status === "running";
  // a render where only some clips failed is still "done", and j.error says how many didn't make it
  const result = j.outcome === "failed" ? j.detail : j.outcome === "stopped" ? j.error : j.outcome === "done" ? j.error ?? "Done" : `${j.pct}% ${j.msg}`;
  const resultColor = j.outcome === "failed" ? "var(--danger)" : j.outcome === "done" && j.error ? "var(--warn)" : undefined;

  return (
    <Fragment>
      <tr>
        <td><JobStatus outcome={j.outcome} /></td>
        <td>{j.kind}</td>
        <td className="cell-clip" title={j.project ?? undefined}>
          {/* the editor only opens your own projects; a link to anyone else's would be a dead end */}
          {j.project_id && j.user === user?.email ? <Link to={`/app/p/${j.project_id}`}>{j.project ?? "—"}</Link> : j.project ?? "—"}
        </td>
        <td className="cell-clip" title={j.user}><Link to={`/admin/users?q=${encodeURIComponent(j.user)}`}>{j.user}</Link></td>
        <td className="cell-clip" title={result ?? undefined} style={resultColor ? { color: resultColor } : undefined}>{result}</td>
        <td className="muted" title={new Date(j.created_at).toLocaleString()} style={{ whiteSpace: "nowrap" }}>{fmtAgo(j.created_at)}</td>
        <td className="muted">{took(j)}</td>
        <td className="cell-actions">
          {live && <ConfirmButton label="Cancel" confirmLabel="Stop job" disabled={cancel.isPending} onConfirm={() => cancel.mutate()} />}
          <button className="link-btn" aria-expanded={open} onClick={() => setOpen((v) => !v)}>{open ? "Hide" : "Details"}</button>
        </td>
      </tr>
      {open && (
        <tr>
          <td colSpan={8} className="admin-row-panel">
            <dl className="admin-facts">
              <div><dt>Job</dt><dd><code>{j.id}</code></dd></div>
              <div><dt>Created</dt><dd>{new Date(j.created_at).toLocaleString()}</dd></div>
              <div><dt>Started</dt><dd>{j.started_at ? new Date(j.started_at).toLocaleString() : "Not yet"}</dd></div>
              <div><dt>Finished</dt><dd>{j.finished_at ? new Date(j.finished_at).toLocaleString() : "Not yet"}</dd></div>
              {j.worker && <div><dt>Worker</dt><dd><code>{j.worker}</code></dd></div>}
              {j.error && <div className="wide"><dt>What the user saw</dt><dd>{j.error}</dd></div>}
              {j.detail && <div className="wide"><dt>What happened</dt><dd><pre className="admin-pre">{j.detail}</pre></dd></div>}
            </dl>
          </td>
        </tr>
      )}
    </Fragment>
  );
}
