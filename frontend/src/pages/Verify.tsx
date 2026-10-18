import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { jpost } from "@/api/client";
import { useAuth } from "@/auth/AuthContext";
import { AuthLayout } from "./AuthLayout";

export function Verify() {
  const [params] = useSearchParams();
  const token = params.get("token");
  const { user, refresh } = useAuth();
  const nav = useNavigate();
  const [state, setState] = useState<"working" | "ok" | "bad" | "pending">(token ? "working" : "pending");
  const ran = useRef(false);

  useEffect(() => {
    if (!token || ran.current) return;
    ran.current = true;
    jpost("/api/auth/verify", { token })
      .then(async () => {
        await refresh();
        setState("ok");
        // a token link means they clicked from email, likely not signed in yet
        setTimeout(() => nav(user ? "/app" : "/login?verified=1", { replace: true }), 1200);
      })
      .catch(() => setState("bad"));
  }, [token, refresh, nav, user]);

  if (state === "working") {
    return (
      <AuthLayout title="Verifying…">
        <div style={{ display: "grid", placeItems: "center" }}>
          <div className="spin" />
        </div>
      </AuthLayout>
    );
  }
  if (state === "ok") {
    return <AuthLayout title="You're in"><p className="muted" style={{ textAlign: "center" }}>Taking you to your projects…</p></AuthLayout>;
  }
  if (state === "bad") {
    return (
      <AuthLayout title="Link expired" footer={<Link to="/login">Back to sign in</Link>}>
        <p className="muted" style={{ fontSize: "var(--fs-sm)", textAlign: "center" }}>
          That verification link is no longer valid. Sign in and we'll send a fresh one.
        </p>
      </AuthLayout>
    );
  }

  // pending — logged in but not verified
  return (
    <AuthLayout title="Verify your email" footer={user ? <ResendLink email={user.email} /> : <Link to="/login">Sign in</Link>}>
      <p className="muted" style={{ fontSize: "var(--fs-sm)", textAlign: "center" }}>
        {user ? <>We sent a link to <b style={{ color: "var(--text)" }}>{user.email}</b>. Click it to unlock your projects.</> : "Open the verification link from your email."}
      </p>
    </AuthLayout>
  );
}

function ResendLink({ email }: { email: string }) {
  const [sent, setSent] = useState(false);
  return sent ? (
    <span>Sent — check your inbox.</span>
  ) : (
    <button
      className="btn ghost"
      style={{ padding: "6px 14px" }}
      onClick={() => {
        jpost("/api/auth/request-verify-token", { email }).catch(() => {});
        setSent(true);
      }}
    >
      Resend the email
    </button>
  );
}
