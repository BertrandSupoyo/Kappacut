import { useEffect, useRef } from "react";
import { mountBackground } from "@/vendor/background.js";

export function Background() {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    let teardown: (() => void) | undefined;
    try {
      teardown = mountBackground(ref.current);
    } catch {
      /* WebGL unavailable — the CSS fallback ground still shows */
    }
    return () => teardown?.();
  }, []);
  return (
    <>
      <div
        ref={ref}
        aria-hidden
        style={{
          position: "fixed",
          inset: 0,
          zIndex: -2,
          background:
            "radial-gradient(1200px 800px at 70% -10%, rgba(141,125,202,.3), transparent 60%), var(--bg)",
        }}
      />
      {/* scrim — keeps text readable wherever the gradient goes bright */}
      <div
        aria-hidden
        style={{
          position: "fixed",
          inset: 0,
          zIndex: -1,
          pointerEvents: "none",
          background:
            "linear-gradient(180deg, rgba(12,11,19,.62) 0%, rgba(12,11,19,.34) 30%, rgba(12,11,19,.5) 70%, rgba(12,11,19,.78) 100%)",
        }}
      />
    </>
  );
}
