import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { jpost } from "@/api/client";
import { AuthLayout } from "./AuthLayout";

export function Reset() {
  const [params] = useSearchParams();
  const token = params.get("token");
  const nav = useNavigate();
  const [password, setPassword] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  if (!token) {
    return (
      <AuthLayout title="Missing token" footer={<Link to="/forgot">Request a new link</Link>}>
        <p className="muted" style={{ textAlign: "center", fontSize: "var(--fs-sm)" }}>Open the reset link from your email.</p>
      </AuthLayout>
    );
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr("");
    if (password.length < 8) return setErr("Use at least 8 characters.");
    setBusy(true);
    try {
      await jpost("/api/auth/reset-password", { token, password });
      nav("/login", { replace: true });
    } catch {
      setErr("That link is invalid or has expired.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthLayout title="Choose a new password" footer={<Link to="/login">Back to sign in</Link>}>
      <form onSubmit={submit} style={{ display: "flex", flexDirection: "column", gap: "var(--sp-4)" }}>
        <div className="field">
          <label htmlFor="pw">New password</label>
          <input id="pw" type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} required minLength={8} />
        </div>
        {err && <div className="err">{err}</div>}
        <button className="btn" disabled={busy}>{busy ? "Saving…" : "Save password"}</button>
      </form>
    </AuthLayout>
  );
}
