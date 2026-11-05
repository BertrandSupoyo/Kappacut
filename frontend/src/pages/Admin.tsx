import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, jdelete, jget, jpost } from "@/api/client";
import { useAuth } from "@/auth/AuthContext";
import { useToast } from "@/components/Toast";
import { fmtAgo } from "@/lib/format";

interface AdminUser {
  id: string;
  email: string;
  is_verified: boolean;
  is_superuser: boolean;
  unlimited?: boolean;
  created_at: string;
  projects: number;
  minutes_used: number;
  minutes_cap: number;
  storage_mb: number;
  storage_cap_mb: number;
  minutes_override: number | null;
  storage_mb_override: number | null;
  takedowns: number;
}

interface AdminJob {
  id: string;
  kind: string;
  status: "queued" | "running" | "done" | "error";
  pct: number;
  msg: string;
  error: string | null;
  /** what really happened; `error` is the sentence the user saw */
  detail: string | null;
  user: string;
  project: string | null;
  project_id: string | null;
  created_at: string;
  finished_at: string | null;
}

const STATUS_COLOR: Record<AdminJob["status"], string> = {
  queued: "var(--amber)",
  running: "var(--cyan)",
  done: "var(--good)",
  error: "var(--danger)",
};

export function AdminOnly({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  if (!user?.is_superuser) {
    return (
      <div className="glass" style={{ padding: "var(--sp-5)", marginTop: "var(--sp-5)" }}>
        <b>Admin isn't available on this account.</b>
        <p className="muted" style={{ fontSize: "var(--fs-sm)" }}>
          It's for superusers. <Link to="/app">Back to projects</Link>
        </p>
      </div>
    );
  }
  return <>{children}</>;
}

export function Admin() {
  return (
    <AdminOnly>
      <div style={{ display: "flex", flexDirection: "column", gap: "var(--sp-5)", margin: "var(--sp-4) 0" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "var(--sp-3)", flexWrap: "wrap" }}>
          <h1 style={{ fontSize: "var(--fs-2xl)" }}>Admin</h1>
          <Link to="/admin/training" className="btn ghost" style={{ marginLeft: "auto", padding: "6px 14px" }}>Training</Link>
        </div>
        <QueueSection />
        <InvitesSection />
        <UsersSection />
        <TakedownsSection />
      </div>
    </AdminOnly>
  );
}

function QueueSection() {
  const qc = useQueryClient();
  const toast = useToast();
  const { data } = useQuery({
    queryKey: ["admin", "jobs"],
    queryFn: () => jget<{ jobs: AdminJob[] }>("/api/admin/jobs?limit=40"),
    refetchInterval: 3000,
  });
  const cancel = useMutation({
    mutationFn: (id: string) => jpost<{ status: string }>(`/api/admin/jobs/${id}/cancel`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "jobs"] }),
    onError: (e) => toast((e as Error).message || "Couldn't cancel the job.", "error"),
  });
  const jobs = data?.jobs ?? [];
  const counts = jobs.reduce<Record<string, number>>((acc, j) => ((acc[j.status] = (acc[j.status] ?? 0) + 1), acc), {});

  return (
    <section className="glass" style={{ padding: "var(--sp-4)" }} aria-labelledby="h-queue">
      <div style={{ display: "flex", alignItems: "baseline", gap: "var(--sp-3)", flexWrap: "wrap", marginBottom: "var(--sp-3)" }}>
        <h2 id="h-queue" style={{ fontSize: "var(--fs-lg)" }}>Queue</h2>
        <span className="muted" style={{ fontSize: "var(--fs-xs)" }}>
          {counts.running ?? 0} running · {counts.queued ?? 0} queued · last {jobs.length} jobs · refreshes every 3 s
        </span>
      </div>
      {jobs.length === 0 ? (
        <p className="muted" style={{ margin: 0 }}>No jobs yet.</p>
      ) : (
        <div className="table-wrap">
          <table className="admin-table">
            <thead>
              <tr><th>Status</th><th>Kind</th><th>Project</th><th>User</th><th>Progress</th><th>Started</th><th /></tr>
            </thead>
            <tbody>
              {jobs.map((j) => (
                <tr key={j.id}>
                  <td>
                    <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                      <span aria-hidden style={{ width: 7, height: 7, borderRadius: "50%", background: STATUS_COLOR[j.status] }} />
                      {j.status}
                    </span>
                  </td>
                  <td>{j.kind}</td>
                  <td className="cell-clip">
                    {j.project_id ? <Link to={`/app/p/${j.project_id}`}>{j.project ?? "—"}</Link> : "deleted"}
                  </td>
                  <td className="cell-clip">{j.user}</td>
                  <td className="cell-clip" title={j.detail ?? j.error ?? j.msg}>
                    {j.status === "error" ? <span style={{ color: "var(--danger)" }}>{j.detail ?? j.error}</span>
                      : j.status === "done" ? "done"
                      : `${j.pct}% ${j.msg}`}
                  </td>
                  <td className="muted">{fmtAgo(j.created_at)}</td>
                  <td>
                    {(j.status === "queued" || j.status === "running") && (
                      <button
                        className="btn danger"
                        style={{ padding: "2px 10px", fontSize: "var(--fs-2xs)" }}
                        disabled={cancel.isPending}
                        onClick={() => confirm(`Cancel this ${j.kind} job for "${j.project ?? "a deleted project"}"?`) && cancel.mutate(j.id)}
                      >
                        Cancel
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

interface AdminInvite {
  id: string;
  email: string;
  registered: boolean;
  created_at: string;
}

function InvitesSection() {
  const qc = useQueryClient();
  const toast = useToast();
  const [email, setEmail] = useState("");
  const { data } = useQuery({
    queryKey: ["admin", "invites"],
    queryFn: () => jget<{ invite_only: boolean; invites: AdminInvite[] }>("/api/admin/invites"),
  });
  const refresh = () => qc.invalidateQueries({ queryKey: ["admin", "invites"] });
  const add = useMutation({
    mutationFn: () => jpost<{ created: boolean }>("/api/admin/invites", { email: email.trim() }),
    onSuccess: (r) => {
      refresh();
      toast(r.created ? `Invited ${email.trim()}` : `${email.trim()} was already invited`);
      setEmail("");
    },
    onError: () => toast("That doesn't look like an email address.", "error"),
  });
  const remove = useMutation({
    mutationFn: (id: string) => jdelete(`/api/admin/invites/${id}`),
    onSuccess: refresh,
    onError: (e) => toast((e as Error).message || "Couldn't remove the invite.", "error"),
  });
  const invites = data?.invites ?? [];

  return (
    <section className="glass" style={{ padding: "var(--sp-4)" }} aria-labelledby="h-invites">
      <div style={{ display: "flex", alignItems: "baseline", gap: "var(--sp-3)", flexWrap: "wrap", marginBottom: "var(--sp-3)" }}>
        <h2 id="h-invites" style={{ fontSize: "var(--fs-lg)" }}>Invites</h2>
        {data && (
          <span className="muted" style={{ fontSize: "var(--fs-xs)" }}>
            {data.invite_only
              ? "Sign-up is invite-only: only these emails can register."
              : "Sign-up is open to anyone. Set INVITE_ONLY=true to limit it to these emails."}
          </span>
        )}
      </div>
      <form
        style={{ display: "flex", gap: "var(--sp-2)", flexWrap: "wrap", marginBottom: "var(--sp-3)" }}
        onSubmit={(e) => { e.preventDefault(); if (email.trim()) add.mutate(); }}
      >
        <input
          className="admin-input"
          style={{ width: "min(320px, 100%)" }}
          type="email"
          aria-label="Email to invite"
          placeholder="name@example.com"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
        <button className="btn ghost" style={{ padding: "4px 14px" }} disabled={!email.trim() || add.isPending}>Invite</button>
      </form>
      {invites.length > 0 && (
        <div className="table-wrap">
          <table className="admin-table" style={{ minWidth: 480 }}>
            <thead>
              <tr><th>Email</th><th>Status</th><th>Invited</th><th /></tr>
            </thead>
            <tbody>
              {invites.map((i) => (
                <tr key={i.id}>
                  <td className="cell-clip">{i.email}</td>
                  <td>{i.registered ? "Signed up" : <span className="muted">Not yet</span>}</td>
                  <td className="muted">{fmtAgo(i.created_at)}</td>
                  <td style={{ textAlign: "right" }}>
                    <button className="btn ghost" style={{ padding: "2px 10px", fontSize: "var(--fs-2xs)" }} disabled={remove.isPending} onClick={() => remove.mutate(i.id)}>
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

interface AdminTakedown {
  id: string;
  user_email: string;
  project: string;
  reference: string;
  created_at: string;
}

function TakedownsSection() {
  const { data } = useQuery({
    queryKey: ["admin", "takedowns"],
    queryFn: () => jget<{ takedowns: AdminTakedown[] }>("/api/admin/takedowns"),
  });
  const rows = data?.takedowns ?? [];
  return (
    <section className="glass" style={{ padding: "var(--sp-4)" }} aria-labelledby="h-takedowns">
      <div style={{ display: "flex", alignItems: "baseline", gap: "var(--sp-3)", flexWrap: "wrap", marginBottom: "var(--sp-3)" }}>
        <h2 id="h-takedowns" style={{ fontSize: "var(--fs-lg)" }}>Copyright removals</h2>
        <span className="muted" style={{ fontSize: "var(--fs-xs)" }}>
          To remove something, open Projects on its owner's row above. This record stays after the account is deleted.
        </span>
      </div>
      {rows.length === 0 ? (
        <p className="muted" style={{ margin: 0, fontSize: "var(--fs-sm)" }}>None yet.</p>
      ) : (
        <div className="table-wrap">
          <table className="admin-table" style={{ minWidth: 560 }}>
            <thead>
              <tr><th>When</th><th>Account</th><th>Project</th><th>Notice</th></tr>
            </thead>
            <tbody>
              {rows.map((t) => (
                <tr key={t.id}>
                  <td className="muted">{fmtAgo(t.created_at)}</td>
                  <td className="cell-clip">{t.user_email}</td>
                  <td className="cell-clip">{t.project}</td>
                  <td className="cell-clip" title={t.reference}>{t.reference}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function UserProjects({ user }: { user: AdminUser }) {
  const qc = useQueryClient();
  const toast = useToast();
  const { data } = useQuery({
    queryKey: ["admin", "user-projects", user.id],
    queryFn: () => jget<{ projects: { id: string; name: string; source_filename: string; status: string; created_at: string }[] }>(
      `/api/admin/users/${user.id}/projects`,
    ),
  });
  const takedown = useMutation({
    mutationFn: ({ id, reference }: { id: string; reference: string }) =>
      jpost<{ takedowns: number }>(`/api/admin/projects/${id}/takedown`, { reference }),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["admin"] });
      toast(`Removed. ${user.email} now has ${r.takedowns} removal${r.takedowns === 1 ? "" : "s"}, and has been emailed.`);
    },
    onError: (e) => toast((e as Error).message || "Couldn't remove the project.", "error"),
  });
  const projects = data?.projects ?? [];

  if (!data) return <div className="spin" />;
  if (!projects.length) return <span className="muted">No projects.</span>;
  return (
    <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 6 }}>
      {projects.map((p) => (
        <li key={p.id} style={{ display: "flex", alignItems: "center", gap: "var(--sp-3)", flexWrap: "wrap" }}>
          <span style={{ flex: "1 1 200px", minWidth: 0, overflowWrap: "anywhere" }}>
            {p.name} <span className="muted">· {p.status} · {fmtAgo(p.created_at)}</span>
          </span>
          <button
            className="btn danger"
            style={{ padding: "2px 10px", fontSize: "var(--fs-2xs)" }}
            disabled={takedown.isPending}
            onClick={() => {
              const reference = window.prompt(
                `Remove "${p.name}" and all its clips after a copyright notice?\n\nThis can't be undone, and ${user.email} will be emailed. Which notice is this for? (who sent it, and when)`,
              );
              if (reference?.trim()) takedown.mutate({ id: p.id, reference: reference.trim() });
            }}
          >
            Remove for copyright
          </button>
        </li>
      ))}
    </ul>
  );
}

function UsersSection() {
  const { data } = useQuery({
    queryKey: ["admin", "users"],
    queryFn: () => jget<{ users: AdminUser[] }>("/api/admin/users"),
  });
  const users = data?.users ?? [];
  return (
    <section className="glass" style={{ padding: "var(--sp-4)" }} aria-labelledby="h-users">
      <div style={{ display: "flex", alignItems: "baseline", gap: "var(--sp-3)", marginBottom: "var(--sp-3)" }}>
        <h2 id="h-users" style={{ fontSize: "var(--fs-lg)" }}>Users</h2>
        <span className="muted" style={{ fontSize: "var(--fs-xs)" }}>{users.length} · overrides replace the plan's caps; leave blank for the default</span>
      </div>
      <div className="table-wrap">
        <table className="admin-table">
          <thead>
            <tr><th>Email</th><th>Projects</th><th>Minutes this month</th><th>Storage</th><th>Removals</th><th>Minutes cap</th><th>Storage cap (MB)</th><th /></tr>
          </thead>
          <tbody>
            {users.map((u) => <UserRow key={u.id} u={u} />)}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function UserRow({ u }: { u: AdminUser }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [minutes, setMinutes] = useState(u.minutes_override?.toString() ?? "");
  const [storage, setStorage] = useState(u.storage_mb_override?.toString() ?? "");
  const [showProjects, setShowProjects] = useState(false);
  const dirty = minutes !== (u.minutes_override?.toString() ?? "") || storage !== (u.storage_mb_override?.toString() ?? "");
  const save = useMutation({
    mutationFn: () =>
      jpost(`/api/admin/users/${u.id}/quota`, {
        minutes: minutes.trim() ? Number(minutes) : null,
        storage_mb: storage.trim() ? Number(storage) : null,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "users"] });
      toast(`Saved limits for ${u.email}`);
    },
    onError: (e) => toast((e as Error).message || "Couldn't save.", "error"),
  });
  const remove = useMutation({
    mutationFn: () => api<{ objects_deleted: number }>(`/api/admin/users/${u.id}`, { method: "DELETE" }),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["admin"] });
      toast(`Deleted ${u.email} and ${r.objects_deleted} stored files`);
    },
    onError: (e) => toast((e as Error).message || "Couldn't delete the user.", "error"),
  });
  const valid = [minutes, storage].every((v) => !v.trim() || (/^\d+$/.test(v.trim()) && Number(v) > 0));

  return (
    <>
    <tr>
      <td className="cell-clip">
        {u.email}
        {u.is_superuser && <span className="pill on" style={{ marginLeft: 6, minHeight: 0, padding: "0 6px" }}>admin</span>}
        {!u.is_verified && <span className="muted" style={{ marginLeft: 6, fontSize: "var(--fs-2xs)" }}>unverified</span>}
      </td>
      <td>
        <button className="link-btn" aria-expanded={showProjects} onClick={() => setShowProjects((v) => !v)}>
          {u.projects}{showProjects ? " ▴" : " ▾"}
        </button>
      </td>
      <td>{u.minutes_used}{u.unlimited ? <span className="muted"> · no limit</span> : ` / ${u.minutes_cap}`}</td>
      <td>{u.storage_mb}{u.unlimited ? <span className="muted"> MB · no limit</span> : ` / ${u.storage_cap_mb} MB`}</td>
      <td style={u.takedowns ? { color: "var(--danger)" } : undefined}>{u.takedowns}</td>
      <td>
        <input
          className="admin-input"
          inputMode="numeric"
          aria-label={`Minutes cap override for ${u.email}`}
          placeholder="plan"
          value={minutes}
          onChange={(e) => setMinutes(e.target.value)}
        />
      </td>
      <td>
        <input
          className="admin-input"
          inputMode="numeric"
          aria-label={`Storage cap override for ${u.email}`}
          placeholder="plan"
          value={storage}
          onChange={(e) => setStorage(e.target.value)}
        />
      </td>
      <td>
        <button className="btn ghost" style={{ padding: "4px 12px" }} disabled={!dirty || !valid || save.isPending} onClick={() => save.mutate()}>
          Save
        </button>
        {!u.is_superuser && (
          <button
            className="btn danger"
            style={{ padding: "4px 12px", marginLeft: 6 }}
            disabled={remove.isPending}
            onClick={() =>
              confirm(`Delete ${u.email}? This permanently removes the account, its ${u.projects} project(s), every clip and uploaded sound. It can't be undone.`) &&
              remove.mutate()
            }
          >
            Delete
          </button>
        )}
      </td>
    </tr>
    {showProjects && (
      <tr>
        <td colSpan={8} style={{ background: "var(--glass-2)" }}>
          <UserProjects user={u} />
        </td>
      </tr>
    )}
    </>
  );
}
