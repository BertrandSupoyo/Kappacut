import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, jget, jpost } from "@/api/client";
import { usePublicConfig } from "@/api/config";
import type { Usage } from "@/api/types";
import { useAuth } from "@/auth/AuthContext";
import { useToast } from "@/components/Toast";

function Meter({ label, used, cap, unit, unlimited }: { label: string; used: number; cap: number; unit?: string; unlimited?: boolean }) {
  const pct = !unlimited && cap > 0 ? Math.min(100, (used / cap) * 100) : 0;
  const hot = pct > 85;
  return (
    <div className="glass" style={{ padding: "var(--sp-4)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: "var(--fs-sm)", marginBottom: unlimited ? 0 : 8 }}>
        <span className="muted">{label}</span>
        <span style={{ fontVariantNumeric: "tabular-nums", color: hot ? "var(--warn)" : "var(--text)" }}>
          {used}{unit}{unlimited ? <span className="muted"> · no limit</span> : <> / {cap}{unit}</>}
        </span>
      </div>
      {!unlimited && (
        <div style={{ height: 5, background: "var(--sub-bg)", borderRadius: 99, overflow: "hidden" }}>
          <div style={{ width: `${pct}%`, height: "100%", background: hot ? "var(--warn)" : "var(--accent)" }} />
        </div>
      )}
    </div>
  );
}

function DeleteAccount() {
  const { refresh } = useAuth();
  const nav = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [password, setPassword] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr("");
    setBusy(true);
    try {
      await jpost("/api/users/me/delete", { password });
      qc.clear();
      await refresh();
      nav("/");
      toast("Your account and everything in it have been deleted.");
    } catch (x) {
      setErr(x instanceof ApiError && x.status !== 500 ? x.message : "Couldn't delete your account. Please try again.");
      setBusy(false);
    }
  }

  if (!open) {
    return <button className="btn danger" style={{ padding: "8px 14px" }} onClick={() => setOpen(true)}>Delete my account</button>;
  }
  return (
    <form onSubmit={submit} style={{ display: "flex", flexDirection: "column", gap: "var(--sp-3)" }}>
      <p style={{ margin: 0, fontSize: "var(--fs-sm)" }}>
        This permanently deletes your account, projects, videos and clips, and stops anything still running. It can't be
        undone, so download anything you want to keep first.
      </p>
      <div className="field">
        <label htmlFor="del-pw">Your password</label>
        <input id="del-pw" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
      </div>
      {err && <div className="err" role="alert">{err}</div>}
      <div style={{ display: "flex", gap: "var(--sp-2)", flexWrap: "wrap" }}>
        <button className="btn danger" disabled={busy || !password} style={{ padding: "8px 14px" }}>
          {busy ? "Deleting…" : "Delete everything"}
        </button>
        <button type="button" className="btn ghost" disabled={busy} style={{ padding: "8px 14px" }} onClick={() => { setOpen(false); setPassword(""); setErr(""); }}>
          Cancel
        </button>
      </div>
    </form>
  );
}

export function Account() {
  const { user } = useAuth();
  const cfg = usePublicConfig();
  const { data } = useQuery({ queryKey: ["usage"], queryFn: () => jget<Usage>("/api/users/me/usage") });

  return (
    <div style={{ maxWidth: 560 }}>
      <h1 style={{ fontSize: "var(--fs-2xl)", margin: "var(--sp-4) 0 var(--sp-5)" }}>Account</h1>
      <div className="glass" style={{ padding: "var(--sp-4)", marginBottom: "var(--sp-5)" }}>
        <div className="muted" style={{ fontSize: "var(--fs-2xs)", textTransform: "uppercase", letterSpacing: ".08em" }}>Signed in as</div>
        <div style={{ fontFamily: "'Space Grotesk'", fontWeight: 600 }}>{user?.email}</div>
        <div className="muted" style={{ fontSize: "var(--fs-xs)", marginTop: 4 }}>Plan: {data?.plan ?? "free"}</div>
      </div>

      {data && (
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--sp-3)" }}>
          <Meter label="Transcription this month" used={data.minutes.used} cap={data.minutes.cap} unit=" min" unlimited={data.unlimited} />
          <Meter label="Storage" used={data.storage_mb.used} cap={data.storage_mb.cap} unit=" MB" unlimited={data.unlimited} />
          <Meter label="Renders today" used={data.renders_today.used} cap={data.renders_today.cap} unlimited={data.unlimited} />
          <Meter label="Projects" used={data.projects.used} cap={data.projects.cap} unlimited={data.unlimited} />
          {data.unlimited && (
            <p className="muted" style={{ fontSize: "var(--fs-xs)", margin: 0 }}>
              Admin account: these limits don't apply. One job runs at a time, as for everyone.
            </p>
          )}
          <p className="muted" style={{ fontSize: "var(--fs-xs)" }}>Max upload: {data.max_upload_mb} MB per file.</p>
        </div>
      )}

      <div style={{ marginTop: "var(--sp-6)" }}>
        <a href="/forgot" className="btn ghost" style={{ padding: "8px 14px" }}>Change password</a>
      </div>

      <section className="glass" style={{ padding: "var(--sp-4)", marginTop: "var(--sp-5)" }} aria-labelledby="h-data">
        <h2 id="h-data" style={{ fontSize: "var(--fs-lg)", marginBottom: "var(--sp-2)" }}>Your data</h2>
        <p className="muted" style={{ fontSize: "var(--fs-sm)", marginTop: 0 }}>
          Download a file with your account details, transcripts, clips and edits, plus links to your videos that work for
          24 hours. Anyone with those links can download the files, so keep it private. See our{" "}
          <Link to="/privacy">Privacy Policy</Link>.
        </p>
        <a href="/api/users/me/export" download className="btn ghost" style={{ padding: "8px 14px" }}>Download my data</a>
      </section>

      <section className="glass" style={{ padding: "var(--sp-4)", marginTop: "var(--sp-4)" }} aria-labelledby="h-delete">
        <h2 id="h-delete" style={{ fontSize: "var(--fs-lg)", marginBottom: "var(--sp-3)" }}>Delete your account</h2>
        {user?.is_superuser ? (
          <p className="muted" style={{ fontSize: "var(--fs-sm)", margin: 0 }}>
            Admin accounts can't be deleted here. Remove the admin role first.
          </p>
        ) : (
          <DeleteAccount />
        )}
      </section>

      {cfg?.support_email && (
        <p className="muted" style={{ fontSize: "var(--fs-sm)", marginTop: "var(--sp-5)" }}>
          Need help? Email <a href={`mailto:${cfg.support_email}`}>{cfg.support_email}</a>.
        </p>
      )}
    </div>
  );
}
