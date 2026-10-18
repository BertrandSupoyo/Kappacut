import { createContext, useCallback, useContext, useRef, useState } from "react";
import type { ReactNode } from "react";

type Toast = { id: number; text: string; kind: "info" | "error" };
const Ctx = createContext<(text: string, kind?: "info" | "error") => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const seq = useRef(0);

  const push = useCallback((text: string, kind: "info" | "error" = "info") => {
    const id = ++seq.current;
    setToasts((t) => [...t, { id, text, kind }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), kind === "error" ? 6000 : 3500);
  }, []);

  return (
    <Ctx.Provider value={push}>
      {children}
      <div
        style={{
          position: "fixed",
          bottom: "var(--sp-5)",
          left: "50%",
          transform: "translateX(-50%)",
          display: "flex",
          flexDirection: "column",
          gap: "var(--sp-2)",
          zIndex: 200,
          alignItems: "center",
        }}
      >
        {toasts.map((t) => (
          <div
            key={t.id}
            className="glass"
            style={{
              padding: "10px 16px",
              fontSize: "var(--fs-sm)",
              borderColor: t.kind === "error" ? "color-mix(in srgb, var(--danger) 50%, transparent)" : undefined,
              color: t.kind === "error" ? "var(--danger)" : "var(--text)",
            }}
          >
            {t.text}
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}

export const useToast = () => useContext(Ctx);
