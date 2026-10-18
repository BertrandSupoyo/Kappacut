import { useQuery } from "@tanstack/react-query";
import { jget } from "@/api/client";
import type { Usage } from "@/api/types";
import { useAuth } from "@/auth/AuthContext";

function Meter({ label, used, cap, unit }: { label: string; used: number; cap: number; unit?: string }) {
  const pct = cap > 0 ? Math.min(100, (used / cap) * 100) : 0;
  const hot = pct > 85;
  return (
    <div className="glass" style={{ padding: "var(--sp-4)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: "var(--fs-sm)", marginBottom: 8 }}>
        <span className="muted">{label}</span>
        <span style={{ fontVariantNumeric: "tabular-nums", color: hot ? "var(--warn)" : "var(--text)" }}>
          {used}{unit} / {cap}{unit}
        </span>
      </div>
      <div style={{ height: 5, background: "var(--sub-bg)", borderRadius: 99, overflow: "hidden" }}>
        <div style={{ width: `${pct}%`, height: "100%", background: hot ? "var(--warn)" : "var(--accent)" }} />
      </div>
    </div>
  );
}

export function Account() {
  const { user } = useAuth();
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
          <Meter label="Transcription this month" used={data.minutes.used} cap={data.minutes.cap} unit=" min" />
          <Meter label="Storage" used={data.storage_mb.used} cap={data.storage_mb.cap} unit=" MB" />
          <Meter label="Renders today" used={data.renders_today.used} cap={data.renders_today.cap} />
          <Meter label="Projects" used={data.projects.used} cap={data.projects.cap} />
          <p className="muted" style={{ fontSize: "var(--fs-xs)" }}>Max upload: {data.max_upload_mb} MB per file.</p>
        </div>
      )}

      <div style={{ marginTop: "var(--sp-6)" }}>
        <a href="/forgot" className="btn ghost" style={{ padding: "8px 14px" }}>Change password</a>
      </div>
    </div>
  );
}
