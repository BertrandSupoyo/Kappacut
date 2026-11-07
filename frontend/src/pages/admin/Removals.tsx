import { useState } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { jget, jpost } from "@/api/client";
import { useToast } from "@/components/Toast";
import { fmtAgo } from "@/lib/format";
import { LoadError, Loading, PageHead, SearchBox, useListParams } from "./ui";

interface FoundProject {
  id: string;
  name: string;
  source_filename: string;
  status: string;
  owner: string;
  created_at: string;
}

interface Takedown {
  id: string;
  user_email: string;
  project: string;
  reference: string;
  created_at: string;
}

/** Permanent, and the owner is told, so it asks which notice this is for before doing anything. */
export function RemoveForCopyright({ project, owner }: { project: { id: string; name: string }; owner: string }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [reference, setReference] = useState("");
  const remove = useMutation({
    mutationFn: () => jpost<{ takedowns: number }>(`/api/admin/projects/${project.id}/takedown`, { reference: reference.trim() }),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["admin"] });
      toast(`Removed. ${owner} now has ${r.takedowns} removal${r.takedowns === 1 ? "" : "s"}, and has been emailed.`);
    },
    onError: (e) => toast((e as Error).message || "Couldn't remove the project.", "error"),
  });

  if (!open) {
    return <button className="btn danger small" onClick={() => setOpen(true)}>Remove for copyright</button>;
  }
  return (
    <form className="panel-inline" onSubmit={(e) => { e.preventDefault(); if (reference.trim()) remove.mutate(); }}>
      <label className="panel-wide">Which notice is this for? Who sent it, and when.
        <input className="admin-input" autoFocus maxLength={500} value={reference} onChange={(e) => setReference(e.target.value)} />
      </label>
      <p className="muted panel-help">This permanently deletes "{project.name}" and its clips, and emails {owner}. It can't be undone.</p>
      <div className="panel-buttons">
        <button className="btn danger small" disabled={!reference.trim() || remove.isPending}>Remove permanently</button>
        <button type="button" className="btn ghost small" onClick={() => setOpen(false)}>Keep</button>
      </div>
    </form>
  );
}

export function AdminRemovals() {
  const { get, set } = useListParams({ q: "" });
  const q = get("q");
  const found = useQuery({
    queryKey: ["admin", "find-projects", q],
    queryFn: () => jget<{ projects: FoundProject[] }>(`/api/admin/projects?q=${encodeURIComponent(q)}`),
    enabled: q.length >= 2,
    placeholderData: keepPreviousData,
  });
  const history = useQuery({
    queryKey: ["admin", "takedowns"],
    queryFn: () => jget<{ takedowns: Takedown[] }>("/api/admin/takedowns"),
  });

  return (
    <div className="admin-page">
      <PageHead title="Copyright removals" note="When a notice arrives, find the project it's about and remove it. The record stays after the account is deleted, for your repeat-infringer policy." />

      <section className="glass admin-card" aria-labelledby="h-find">
        <div className="admin-card-head"><h2 id="h-find">Find a project</h2></div>
        <div className="admin-filters" style={{ marginBottom: "var(--sp-3)" }}>
          <SearchBox value={q} onChange={(v) => set({ q: v })} label="Find a project" placeholder="Project name, file name or owner's email" />
        </div>
        {q.length < 2 ? (
          <p className="muted admin-none">Type at least 2 characters.</p>
        ) : found.isPending ? (
          <Loading />
        ) : !found.data ? (
          <LoadError query={found} what="the search" />
        ) : found.data.projects.length === 0 ? (
          <p className="muted admin-none">No projects match "{q}".</p>
        ) : (
          <ul className="panel-projects">
            {found.data.projects.map((p) => (
              <li key={p.id}>
                <span style={{ flex: "1 1 240px", minWidth: 0, overflowWrap: "anywhere" }}>
                  {p.name}
                  <span className="muted"> · {p.source_filename} · {p.owner} · {fmtAgo(p.created_at)}</span>
                </span>
                <RemoveForCopyright project={p} owner={p.owner} />
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="glass admin-card" aria-labelledby="h-history">
        <div className="admin-card-head"><h2 id="h-history">Removed so far</h2></div>
        {history.isPending ? (
          <Loading />
        ) : !history.data ? (
          <LoadError query={history} what="the removals" />
        ) : history.data.takedowns.length === 0 ? (
          <p className="muted admin-none">None yet.</p>
        ) : (
          <div className="table-wrap">
            <table className="admin-table" style={{ minWidth: 560 }}>
              <thead>
                <tr><th>When</th><th>Account</th><th>Project</th><th>Notice</th></tr>
              </thead>
              <tbody>
                {history.data.takedowns.map((t) => (
                  <tr key={t.id}>
                    <td className="muted" title={new Date(t.created_at).toLocaleString()}>{fmtAgo(t.created_at)}</td>
                    <td className="cell-clip" title={t.user_email}>{t.user_email}</td>
                    <td className="cell-clip" title={t.project}>{t.project}</td>
                    <td className="cell-clip" title={t.reference}>{t.reference}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
