import { useState } from "react";
import { Link } from "react-router-dom";
import { jpost } from "@/api/client";
import { AuthLayout } from "./AuthLayout";

export function Forgot() {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    await jpost("/api/auth/forgot-password", { email: email.trim() }).catch(() => {});
    setSent(true);
    setBusy(false);
  }

  if (sent) {
    return (
      <AuthLayout title="Check your email" footer={<Link to="/login">Back to sign in</Link>}>
        <p className="muted" style={{ fontSize: "var(--fs-sm)", textAlign: "center" }}>
          If an account exists for that address, a reset link is on its way.
        </p>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout title="Reset your password" footer={<Link to="/login">Back to sign in</Link>}>
      <form onSubmit={submit} style={{ display: "flex", flexDirection: "column", gap: "var(--sp-4)" }}>
        <div className="field">
          <label htmlFor="email">Email</label>
          <input id="email" type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </div>
        <button className="btn" disabled={busy}>{busy ? "Sending…" : "Send reset link"}</button>
      </form>
    </AuthLayout>
  );
}
