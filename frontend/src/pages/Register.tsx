import { useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, jpost } from "@/api/client";
import { AuthLayout } from "./AuthLayout";

export function Register() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr("");
    if (password.length < 8) return setErr("Use at least 8 characters.");
    setBusy(true);
    try {
      await jpost("/api/auth/register", { email: email.trim(), password });
      setDone(true);
    } catch (x) {
      if (x instanceof ApiError && x.status === 400) setErr("That email is already registered.");
      else if (x instanceof ApiError && x.status === 429) setErr("Too many sign-ups from here — try again later.");
      else setErr("Could not create the account.");
    } finally {
      setBusy(false);
    }
  }

  if (done) {
    return (
      <AuthLayout title="Check your email" footer={<Link to="/login">Back to sign in</Link>}>
        <p className="muted" style={{ fontSize: "var(--fs-sm)", textAlign: "center" }}>
          We sent a verification link to <b style={{ color: "var(--text)" }}>{email}</b>. Click it to activate your account.
        </p>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout title="Create your account" footer={<>Already have one? <Link to="/login">Sign in</Link></>}>
      <form onSubmit={submit} style={{ display: "flex", flexDirection: "column", gap: "var(--sp-4)" }}>
        <div className="field">
          <label htmlFor="email">Email</label>
          <input id="email" type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </div>
        <div className="field">
          <label htmlFor="pw">Password</label>
          <input id="pw" type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} required minLength={8} />
        </div>
        {err && <div className="err">{err}</div>}
        <button className="btn" disabled={busy}>{busy ? "Creating…" : "Create account"}</button>
      </form>
    </AuthLayout>
  );
}
