import type { ReactNode } from "react";
import { Link, NavLink, Outlet } from "react-router-dom";
import { useAuth } from "@/auth/AuthContext";
import { useOverview } from "./admin/api";

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
      <div className="admin-shell">
        <AdminNav />
        <div style={{ minWidth: 0 }}>
          <Outlet />
        </div>
      </div>
    </AdminOnly>
  );
}

function AdminNav() {
  const { data } = useOverview();
  const urgent = data?.attention.filter((a) => a.level === "critical").length ?? 0;
  const active = (data?.queue.running ?? 0) + (data?.queue.queued ?? 0);
  const links: { to: string; label: string; end?: boolean; count?: number; alert?: boolean; countLabel?: string }[] = [
    { to: "/admin", label: "Overview", end: true, count: urgent, alert: true, countLabel: "urgent problems" },
    { to: "/admin/jobs", label: "Jobs", count: active, countLabel: "active jobs" },
    { to: "/admin/users", label: "Users" },
    { to: "/admin/invites", label: "Invites" },
    { to: "/admin/removals", label: "Removals" },
    { to: "/admin/training", label: "Training" },
  ];
  return (
    <nav className="admin-nav" aria-label="Admin sections">
      {links.map((l) => (
        <NavLink key={l.to} to={l.to} end={l.end}>
          {l.label}
          {!!l.count && <span className={`admin-count${l.alert ? " alert" : ""}`} aria-label={`${l.count} ${l.countLabel}`}>{l.count}</span>}
        </NavLink>
      ))}
    </nav>
  );
}
