import { Link } from "react-router-dom";

/** The chrome "clipfinder" wordmark, ported from web/index.html. */
export function Wordmark({ to = "/", size = 20 }: { to?: string; size?: number }) {
  return (
    <Link
      to={to}
      style={{
        fontFamily: "'Space Grotesk', sans-serif",
        fontWeight: 700,
        fontSize: size,
        letterSpacing: "-0.03em",
        background: "var(--chrome)",
        WebkitBackgroundClip: "text",
        backgroundClip: "text",
        color: "transparent",
        textShadow: "0 1px 0 rgba(0,0,0,.55), 0 0 14px rgba(6,4,14,.42)",
      }}
    >
      clipfinder
    </Link>
  );
}
