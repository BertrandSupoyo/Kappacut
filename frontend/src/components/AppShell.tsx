import type { ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "@/auth/AuthContext";
import { Wordmark } from "./Wordmark";

export function AppShell({ children }: { children: ReactNode }) {
  const { user, logout } = useAuth();
  const nav = useNavigate();

  return (
    <div style={{ minHeight: "100%", display: "flex", flexDirection: "column" }}>
      <header
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--sp-4)",
          padding: "var(--sp-4) 0",
        }}
        className="wrap"
      >
        <Wordmark to="/app" size={18} />
        <nav style={{ marginLeft: "auto", display: "flex", gap: "var(--sp-4)", alignItems: "center", fontSize: "var(--fs-sm)" }}>
          <Link to="/app" className="muted">Projects</Link>
          {user?.is_superuser && <Link to="/admin" className="muted">Admin</Link>}
          <Link to="/account" className="muted">Account</Link>
          <button
            className="btn ghost"
            style={{ padding: "6px 12px" }}
            onClick={async () => {
              await logout();
              nav("/");
            }}
          >
            Sign out
          </button>
        </nav>
      </header>
      <main className="wrap" style={{ flex: 1, paddingBottom: "var(--sp-6)" }}>
        {children}
      </main>
    </div>
  );
}
