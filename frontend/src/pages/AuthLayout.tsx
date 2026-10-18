import type { ReactNode } from "react";
import { Wordmark } from "@/components/Wordmark";

export function AuthLayout({ title, children, footer }: { title: string; children: ReactNode; footer?: ReactNode }) {
  return (
    <div style={{ minHeight: "100%", display: "grid", placeItems: "center", padding: "var(--sp-5)" }}>
      <div className="glass" style={{ width: "min(400px, 100%)", padding: "var(--sp-6)" }}>
        <div style={{ textAlign: "center", marginBottom: "var(--sp-5)" }}>
          <Wordmark size={22} />
        </div>
        <h1 style={{ fontSize: "var(--fs-xl)", textAlign: "center", marginBottom: "var(--sp-5)" }}>{title}</h1>
        {children}
        {footer && (
          <div style={{ marginTop: "var(--sp-4)", textAlign: "center", fontSize: "var(--fs-sm)" }} className="muted">
            {footer}
          </div>
        )}
      </div>
    </div>
  );
}
