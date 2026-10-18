import { Link } from "react-router-dom";
import { Wordmark } from "@/components/Wordmark";

export function NotFound() {
  return (
    <div style={{ minHeight: "100%", display: "grid", placeItems: "center", padding: "var(--sp-5)", textAlign: "center" }}>
      <div>
        <Wordmark size={20} />
        <h1 style={{ fontSize: "var(--fs-2xl)", margin: "var(--sp-4) 0" }}>Nothing here</h1>
        <Link className="btn" to="/">Back home</Link>
      </div>
    </div>
  );
}
