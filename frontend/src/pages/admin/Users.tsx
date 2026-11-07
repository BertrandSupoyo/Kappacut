import { Fragment, useState } from "react";
import { Link } from "react-router-dom";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, jget, jpost } from "@/api/client";
import { useToast } from "@/components/Toast";
import { fmtAgo } from "@/lib/format";
import type { AdminUser } from "./api";
import { RemoveForCopyright } from "./Removals";
import { ConfirmButton, fmtBytes, LoadError, Loading, PageHead, PAGE_SIZE, Pager, SearchBox, UsageMeter, useListParams } from "./ui";

const minutes = (v: number) => Math.round(v * 10) / 10;
const storage = (mb: number) => fmtBytes(mb * 1024 ** 2);

const FILTERS = [
  ["all", "Everyone"],
  ["near_limit", "Near a limit"],
  ["removals", "Has copyright removals"],
  ["unverified", "Email not confirmed"],
  ["suspended", "Suspended"],
  ["admins", "Admins"],
] as const;

const SORTS = [
  ["newest", "Newest first"],
  ["active", "Recently active"],
  ["minutes", "Most minutes"],
  ["storage", "Most storage"],
  ["removals", "Most removals"],
] as const;

export function AdminUsers() {
  const { get, set, page, setPage } = useListParams({ q: "", show: "all", sort: "newest" });
  const q = get("q"), show = get("show"), sort = get("sort");
  const query = useQuery({
    queryKey: ["admin", "users", q, show, sort, page],
    queryFn: () => {
      const params = new URLSearchParams({ q, show, sort, limit: String(PAGE_SIZE), offset: String((page - 1) * PAGE_SIZE) });
      return jget<{ users: AdminUser[]; total: number }>(`/api/admin/users?${params}`);
    },
    placeholderData: keepPreviousData,
  });
  const data = query.data;

  return (
    <div className="admin-page">
      <PageHead title="Users" note={data ? `${data.total.toLocaleString()} ${data.total === 1 ? "account" : "accounts"}. Open one to change its limits, or to suspend or delete it.` : undefined} />
      <div className="admin-filters">
        <SearchBox value={q} onChange={(v) => set({ q: v })} label="Search by email" placeholder="Search by email" />
        <select className="admin-input admin-select" aria-label="Show" value={show} onChange={(e) => set({ show: e.target.value })}>
          {FILTERS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
        <select className="admin-input admin-select" aria-label="Sort by" value={sort} onChange={(e) => set({ sort: e.target.value })}>
          {SORTS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </div>

      {query.isPending ? (
        <Loading />
      ) : !data ? (
        <LoadError query={query} what="the users" />
      ) : data.users.length === 0 ? (
        <p className="muted admin-none">No accounts match this.</p>
      ) : (
        <section className="glass admin-card" style={{ opacity: query.isPlaceholderData ? 0.6 : 1 }} aria-label="Users">
          <div className="table-wrap">
            <table className="admin-table">
              <thead>
                <tr><th>Account</th><th>Joined</th><th>Last active</th><th>Projects</th><th>Minutes this month</th><th>Storage</th><th>Removals</th></tr>
              </thead>
              <tbody>
                {data.users.map((u) => <UserRow key={u.id} u={u} />)}
              </tbody>
            </table>
          </div>
        </section>
      )}
      {data && <Pager total={data.total} page={page} onPage={setPage} />}
    </div>
  );
}

function UserRow({ u }: { u: AdminUser }) {
  const [open, setOpen] = useState(false);
  return (
    <Fragment>
      <tr>
        <td className="cell-clip" title={u.email} style={{ maxWidth: 260 }}>
          <button className="link-btn row-open" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
            {u.email}<span aria-hidden>{open ? " ▴" : " ▾"}</span>
          </button>
          <div className="tag-row">
            {u.is_superuser && <span className="tag">Admin</span>}
            {u.suspended && <span className="tag danger">Suspended</span>}
            {!u.is_verified && <span className="tag">Not confirmed</span>}
          </div>
        </td>
        <td className="muted" title={new Date(u.created_at).toLocaleString()} style={{ whiteSpace: "nowrap" }}>{fmtAgo(u.created_at)}</td>
        <td className="muted" style={{ whiteSpace: "nowrap" }}>{u.last_active ? fmtAgo(u.last_active) : "Never"}</td>
        <td>{u.projects}</td>
        <td>
          {u.unlimited ? <>{minutes(u.minutes_used)}<span className="muted"> · no limit</span></> : (
            <>{minutes(u.minutes_used)} / {u.minutes_cap}<UsageMeter used={u.minutes_used} cap={u.minutes_cap} /></>
          )}
        </td>
        <td>
          {u.unlimited ? <>{storage(u.storage_mb)}<span className="muted"> · no limit</span></> : (
            <>{storage(u.storage_mb)} / {storage(u.storage_cap_mb)}<UsageMeter used={u.storage_mb} cap={u.storage_cap_mb} /></>
          )}
        </td>
        <td style={u.takedowns ? { color: "var(--danger)" } : undefined}>{u.takedowns}</td>
      </tr>
      {open && (
        <tr>
          <td colSpan={7} className="admin-row-panel"><UserPanel u={u} /></td>
        </tr>
      )}
    </Fragment>
  );
}

function UserPanel({ u }: { u: AdminUser }) {
  return (
    <div className="user-panel">
      <dl className="admin-facts">
        <div><dt>Joined</dt><dd>{new Date(u.created_at).toLocaleDateString()}</dd></div>
        <div><dt>Email</dt><dd>{u.is_verified ? "Confirmed" : "Not confirmed"}</dd></div>
        <div>
          <dt>Terms accepted</dt>
          <dd>{u.terms_accepted_at ? `${new Date(u.terms_accepted_at).toLocaleDateString()} (version ${u.terms_version})` : "No record"}</dd>
        </div>
        <div><dt>Jobs</dt><dd><Link to={`/admin/jobs?q=${encodeURIComponent(u.email)}`}>See their jobs</Link></dd></div>
        {u.suspended && (
          <div className="wide">
            <dt>Suspended {u.suspended_at ? fmtAgo(u.suspended_at) : ""}</dt>
            <dd>{u.suspended_reason ?? "No reason was recorded, and they weren't emailed."}</dd>
          </div>
        )}
      </dl>
      {!u.unlimited && <LimitsForm u={u} />}
      <div>
        <h3 className="panel-title">Projects</h3>
        <UserProjects u={u} />
      </div>
      {!u.is_superuser && <AccountActions u={u} />}
    </div>
  );
}

function LimitsForm({ u }: { u: AdminUser }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [minutes, setMinutes] = useState(u.minutes_override?.toString() ?? "");
  const [storage, setStorage] = useState(u.storage_mb_override?.toString() ?? "");
  const dirty = minutes !== (u.minutes_override?.toString() ?? "") || storage !== (u.storage_mb_override?.toString() ?? "");
  const valid = [minutes, storage].every((v) => !v.trim() || (/^\d+$/.test(v.trim()) && Number(v) > 0));
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
  return (
    <form className="panel-form" onSubmit={(e) => { e.preventDefault(); if (dirty && valid) save.mutate(); }}>
      <h3 className="panel-title">Limits</h3>
      <p className="muted panel-help">Leave a box empty to use the plan's limit.</p>
      <div className="panel-fields">
        <label>Minutes per month
          <input className="admin-input" inputMode="numeric" placeholder="plan" value={minutes} onChange={(e) => setMinutes(e.target.value)} />
        </label>
        <label>Storage (MB)
          <input className="admin-input" inputMode="numeric" placeholder="plan" value={storage} onChange={(e) => setStorage(e.target.value)} />
        </label>
        <button className="btn ghost small" disabled={!dirty || !valid || save.isPending}>Save limits</button>
      </div>
      {!valid && <p className="panel-error" role="alert">Limits must be whole numbers above zero.</p>}
    </form>
  );
}

function UserProjects({ u }: { u: AdminUser }) {
  const query = useQuery({
    queryKey: ["admin", "user-projects", u.id],
    queryFn: () => jget<{ projects: { id: string; name: string; status: string; created_at: string }[] }>(`/api/admin/users/${u.id}/projects`),
  });
  if (query.isPending) return <div className="spin" />;
  if (!query.data) return <LoadError query={query} what="the projects" />;
  if (!query.data.projects.length) return <p className="muted admin-none">No projects.</p>;
  return (
    <ul className="panel-projects">
      {query.data.projects.map((p) => (
        <li key={p.id}>
          <span style={{ flex: "1 1 200px", minWidth: 0, overflowWrap: "anywhere" }}>
            {p.name} <span className="muted">· {p.status} · {fmtAgo(p.created_at)}</span>
          </span>
          <RemoveForCopyright project={p} owner={u.email} />
        </li>
      ))}
    </ul>
  );
}

function AccountActions({ u }: { u: AdminUser }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [suspending, setSuspending] = useState(false);
  const [reason, setReason] = useState("");
  const done = (message: string) => {
    qc.invalidateQueries({ queryKey: ["admin"] });
    toast(message);
  };
  const failed = (fallback: string) => (e: unknown) => toast((e as Error).message || fallback, "error");
  const suspend = useMutation({
    mutationFn: () => jpost(`/api/admin/users/${u.id}/suspend`, { reason: reason.trim() }),
    onSuccess: () => {
      setSuspending(false);
      done(reason.trim() ? `Suspended ${u.email} and emailed them the reason.` : `Suspended ${u.email}. They weren't emailed.`);
      setReason("");
    },
    onError: failed("Couldn't suspend the account."),
  });
  const reinstate = useMutation({
    mutationFn: () => jpost(`/api/admin/users/${u.id}/reinstate`),
    onSuccess: () => done(`${u.email} can sign in again.`),
    onError: failed("Couldn't reinstate the account."),
  });
  const remove = useMutation({
    mutationFn: () => api<{ objects_deleted: number }>(`/api/admin/users/${u.id}`, { method: "DELETE" }),
    onSuccess: (r) => done(`Deleted ${u.email} and ${r.objects_deleted} stored files.`),
    onError: failed("Couldn't delete the user."),
  });

  return (
    <div>
      <h3 className="panel-title">Account</h3>
      {suspending ? (
        <form className="panel-inline" onSubmit={(e) => { e.preventDefault(); suspend.mutate(); }}>
          <label className="panel-wide">Why? This is emailed to them.
            <input className="admin-input" autoFocus maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} />
          </label>
          <p className="muted panel-help">They'll be signed out and their jobs will stop. Their files are kept. Leave the reason empty only if the law or safety stops you telling them: no email is sent.</p>
          <div className="panel-buttons">
            <button className="btn danger small" disabled={suspend.isPending}>Suspend account</button>
            <button type="button" className="btn ghost small" onClick={() => setSuspending(false)}>Keep</button>
          </div>
        </form>
      ) : (
        <div className="panel-buttons">
          {u.suspended ? (
            <button className="btn ghost small" disabled={reinstate.isPending} onClick={() => reinstate.mutate()}>Reinstate</button>
          ) : (
            <button className="btn ghost small" onClick={() => setSuspending(true)}>Suspend</button>
          )}
          <ConfirmButton
            label="Delete account"
            confirmLabel={`Delete it and its ${u.projects} project${u.projects === 1 ? "" : "s"}`}
            disabled={remove.isPending}
            onConfirm={() => remove.mutate()}
          />
        </div>
      )}
    </div>
  );
}
