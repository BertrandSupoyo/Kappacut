import type { ReactNode } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { useAuth } from "@/auth/AuthContext";
import { SiteFooter } from "./SiteFooter";
import { Wordmark } from "./Wordmark";

const navClass = ({ isActive }: { isActive: boolean }) => (isActive ? "nav-on" : "muted");

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
        className="wrap app-header"
      >
        <Wordmark to="/app" size={18} />
        <nav className="app-nav" style={{ marginLeft: "auto", display: "flex", alignItems: "center", fontSize: "var(--fs-sm)" }}>
          <NavLink to="/app" end className={navClass}>Projects</NavLink>
          <NavLink to="/app/agent" className={navClass}>AI agent</NavLink>
          {user?.is_superuser && <NavLink to="/admin" className={navClass}>Admin</NavLink>}
          <NavLink to="/account" className={navClass}>Account</NavLink>
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
      <SiteFooter />
    </div>
  );
}
