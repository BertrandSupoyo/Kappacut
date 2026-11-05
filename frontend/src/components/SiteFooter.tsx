import { Link } from "react-router-dom";
import { usePublicConfig } from "@/api/config";

export function SiteFooter() {
  const cfg = usePublicConfig();
  return (
    <footer className="site-footer">
      <Link to="/terms">Terms</Link>
      <Link to="/privacy">Privacy</Link>
      <Link to="/copyright">Copyright</Link>
      {cfg?.support_email && <a href={`mailto:${cfg.support_email}`}>Contact</a>}
    </footer>
  );
}
