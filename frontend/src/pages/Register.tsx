import { useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, jpost } from "@/api/client";
import { usePublicConfig } from "@/api/config";
import { AuthLayout } from "./AuthLayout";

export function Register() {
  const cfg = usePublicConfig();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [accepted, setAccepted] = useState(false);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr("");
    if (password.length < 8) return setErr("Use at least 8 characters.");
    if (!accepted) return setErr("Please accept the Terms and Privacy Policy to continue.");
    setBusy(true);
    try {
      await jpost("/api/auth/register", { email: email.trim(), password, accept_terms: true });
      setDone(true);
    } catch (x) {
      const detail = x instanceof ApiError && x.data && typeof x.data === "object" && "detail" in x.data ? x.data.detail : null;
      if (detail === "REGISTER_USER_ALREADY_EXISTS") setErr("That email is already registered.");
      else if (x instanceof ApiError && x.status === 403) setErr(x.message || "Could not create the account.");
      else if (x instanceof ApiError && x.status === 429) setErr("Too many sign-ups from here. Try again later.");
      else if (x instanceof ApiError && x.status === 400 && typeof detail === "string") setErr(detail);
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
        {cfg?.invite_only && (
          <p className="muted" style={{ fontSize: "var(--fs-sm)", margin: 0 }}>
            Kappacut is invite-only right now. Use the email address your invite was sent to.
          </p>
        )}
        <div className="field">
          <label htmlFor="email">Email</label>
          <input id="email" type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </div>
        <div className="field">
          <label htmlFor="pw">Password</label>
          <input id="pw" type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} required minLength={8} />
        </div>
        <label className="check-row">
          <input type="checkbox" checked={accepted} onChange={(e) => setAccepted(e.target.checked)} required />
          <span>
            I'm at least 16 and I accept the <Link to="/terms" target="_blank">Terms</Link> and{" "}
            <Link to="/privacy" target="_blank">Privacy Policy</Link>.
          </span>
        </label>
        {err && <div className="err">{err}</div>}
        <button className="btn" disabled={busy}>{busy ? "Creating…" : "Create account"}</button>
      </form>
    </AuthLayout>
  );
}
