import type { ReactNode } from "react";

export function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6, marginBottom: "var(--sp-3)" }}>
      <span style={{ fontFamily: "'Space Grotesk'", fontSize: "var(--fs-2xs)", fontWeight: 600, letterSpacing: ".06em", textTransform: "uppercase", color: "var(--text-mut)" }}>
        {label}
      </span>
      {children}
    </div>
  );
}

export function Seg<T extends string>({
  options,
  value,
  onChange,
}: {
  options: { id: T; label: string }[] | readonly T[];
  value: T;
  onChange: (v: T) => void;
}) {
  const opts = (options as (T | { id: T; label: string })[]).map((o) =>
    typeof o === "string" ? { id: o, label: o } : o,
  );
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
      {opts.map((o) => (
        <button
          key={o.id}
          onClick={() => onChange(o.id)}
          style={{
            padding: "5px 10px",
            fontSize: "var(--fs-xs)",
            borderRadius: "var(--r-xs)",
            border: `1px solid ${value === o.id ? "var(--accent)" : "var(--border)"}`,
            background: value === o.id ? "rgba(169,155,255,.16)" : "transparent",
            color: value === o.id ? "var(--text)" : "var(--text-mut)",
            cursor: "pointer",
            textTransform: "capitalize",
          }}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Slider({
  value,
  min,
  max,
  step,
  onChange,
  fmt,
}: {
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (v: number) => void;
  fmt?: (v: number) => string;
}) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: "var(--sp-3)" }}>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(+e.target.value)}
        style={{ flex: 1, accentColor: "var(--accent)" }}
      />
      <span className="muted" style={{ fontSize: "var(--fs-2xs)", fontVariantNumeric: "tabular-nums", width: 40, textAlign: "right" }}>
        {fmt ? fmt(value) : value}
      </span>
    </div>
  );
}

export function Toggle({ on, onChange, label }: { on: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <label style={{ display: "flex", alignItems: "center", gap: "var(--sp-2)", cursor: "pointer", fontSize: "var(--fs-sm)" }}>
      <span
        onClick={() => onChange(!on)}
        style={{
          width: 34,
          height: 19,
          borderRadius: 99,
          background: on ? "var(--accent)" : "var(--sub-bg)",
          border: "1px solid var(--border)",
          position: "relative",
          transition: "background .15s",
          flexShrink: 0,
        }}
      >
        <span
          style={{
            position: "absolute",
            top: 2,
            left: on ? 16 : 2,
            width: 13,
            height: 13,
            borderRadius: "50%",
            background: on ? "var(--on-accent)" : "var(--text-mut)",
            transition: "left .15s",
          }}
        />
      </span>
      {label}
    </label>
  );
}
