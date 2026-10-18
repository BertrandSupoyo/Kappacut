import { Link } from "react-router-dom";

/** The "kappacut" wordmark: plain white, no gradient or shadow. */
export function Wordmark({ to = "/", size = 20 }: { to?: string; size?: number }) {
  return (
    <Link
      to={to}
      aria-label="Kappacut home"
      style={{
        fontFamily: "'Space Grotesk', sans-serif",
        fontWeight: 700,
        fontSize: size,
        letterSpacing: "-0.03em",
        color: "#ffffff",
      }}
    >
      kappacut
    </Link>
  );
}
