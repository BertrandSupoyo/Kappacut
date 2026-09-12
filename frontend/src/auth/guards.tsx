import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "./AuthContext";

function Loading() {
  return (
    <div style={{ display: "grid", placeItems: "center", minHeight: "60vh" }}>
      <div className="spin" />
    </div>
  );
}

export function RequireVerified({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  const loc = useLocation();
  if (loading) return <Loading />;
  if (!user) return <Navigate to="/login" state={{ from: loc.pathname }} replace />;
  if (!user.is_verified) return <Navigate to="/verify" replace />;
  return <>{children}</>;
}
