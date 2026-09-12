import { Link } from "react-router-dom";
import { useAuth } from "@/auth/AuthContext";
import { Wordmark } from "@/components/Wordmark";

const STEPS = [
  { n: "01", c: "var(--violet-deep)", t: "Upload a video", d: "A stream VOD, a compilation, an interview — up to 2 GB. Resumable, so a dropped connection doesn't cost you the upload." },
  { n: "02", c: "var(--cyan)", t: "Get ranked highlights", d: "Every line transcribed, garbled audio filtered out, an LLM scores the standalone moments and tells you why each one lands." },
  { n: "03", c: "var(--pink)", t: "Trim & render", d: "Adjust the in/out points, edit the auto-captions, add a 9:16 reframe or sound effects, then render. Captions burned in." },
];

const FEATURES = [
  ["Transcript-first picking", "Whisper transcribes the whole thing; the picker works from the words, not guesses. Repetitive hallucinations and dead air are dropped before scoring."],
  ["A reason for every clip", "Each pick comes with its hook, its payoff line, and a one-line why — so you can skim the shortlist instead of watching all of it."],
  ["9:16, captions, SFX", "Reframe to vertical with a blurred fill or a subject crop, pick a caption style, drop in whooshes and impacts — or let the analysis suggest them per clip."],
  ["Your library stays yours", "Projects, analyses and rendered clips live in your account. Only the audio is sent out for transcription."],
];

const FAQ = [
  ["Is it free?", "Yes. The free plan covers 60 minutes of transcription a month, 2 GB per file, and keeps your projects for about a month of inactivity."],
  ["What leaves my machine?", "The extracted audio goes to Groq for transcription and the transcript text is sent to the model that picks moments. Your video file and rendered clips stay in your account."],
  ["How long does a video take?", "A few minutes for a typical stream VOD — most of it is transcription. You watch the progress live."],
  ["Can I bring my own clips to caption?", "Right now clipfinder starts from one uploaded source per project. Multi-source stitching isn't in yet."],
];

export function Landing() {
  const { user } = useAuth();
  const appHref = user ? "/app" : "/register";

  return (
    <div>
      <header className="wrap" style={{ display: "flex", alignItems: "center", gap: "var(--sp-5)", padding: "var(--sp-4) 0" }}>
        <Wordmark />
        <nav style={{ marginLeft: "auto", display: "flex", gap: "var(--sp-5)", alignItems: "center", fontSize: "var(--fs-sm)" }}>
          <a href="#how" className="muted">How it works</a>
          <a href="#features" className="muted">Features</a>
          <a href="#faq" className="muted">FAQ</a>
          {user ? (
            <Link className="btn" to="/app">Open app</Link>
          ) : (
            <>
              <Link to="/login" className="muted">Sign in</Link>
              <Link className="btn" to="/register">Get started</Link>
            </>
          )}
        </nav>
      </header>

      <section className="wrap" style={{ padding: "clamp(48px, 9vw, 120px) 0 var(--sp-6)", maxWidth: 720, marginInline: "auto", textAlign: "center", textShadow: "0 2px 24px rgba(6,4,14,.7)" }}>
        <h1 style={{ fontSize: "var(--fs-3xl)" }}>
          Stop scrubbing.<br />
          Start <span style={{ background: "var(--chrome)", WebkitBackgroundClip: "text", backgroundClip: "text", color: "transparent", textShadow: "none" }}>clipping.</span>
        </h1>
        <p style={{ color: "var(--text-dim)", fontSize: "var(--fs-md)", maxWidth: "48ch", margin: "var(--sp-4) auto var(--sp-5)" }}>
          clipfinder transcribes an entire VOD or compilation, ranks the moments that stand on their own,
          and hands you a shortlist you can trim and render into short-form clips.
        </p>
        <div style={{ display: "flex", gap: "var(--sp-3)", justifyContent: "center", flexWrap: "wrap" }}>
          <Link className="btn" to={appHref} style={{ padding: "12px 24px" }}>
            {user ? "Open the app" : "Get started — it's free"}
          </Link>
          <a className="btn ghost" href="#how" style={{ padding: "12px 24px" }}>How it works</a>
        </div>
        <p className="muted" style={{ fontSize: "var(--fs-xs)", marginTop: "var(--sp-4)" }}>
          Free plan · 60 min/month · no card
        </p>
      </section>

      <section id="how" className="wrap" style={{ padding: "var(--sp-6) 0" }}>
        <h2 style={{ fontSize: "var(--fs-2xl)", textAlign: "center", marginBottom: "var(--sp-5)" }}>Three steps, one pass</h2>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: "var(--sp-4)" }}>
          {STEPS.map((s) => (
            <div key={s.n} className="glass" style={{ padding: "var(--sp-5)" }}>
              <div style={{ fontFamily: "'Space Grotesk'", fontWeight: 700, color: s.c, fontSize: "var(--fs-lg)" }}>{s.n}</div>
              <h3 style={{ fontSize: "var(--fs-lg)", margin: "var(--sp-2) 0 var(--sp-2)" }}>{s.t}</h3>
              <p className="muted" style={{ fontSize: "var(--fs-sm)", margin: 0 }}>{s.d}</p>
            </div>
          ))}
        </div>
      </section>

      <section id="features" className="wrap" style={{ padding: "var(--sp-6) 0" }}>
        <h2 style={{ fontSize: "var(--fs-2xl)", textAlign: "center", marginBottom: "var(--sp-5)" }}>What's in the box</h2>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: "var(--sp-4)" }}>
          {FEATURES.map(([t, d]) => (
            <div key={t} className="glass" style={{ padding: "var(--sp-4)" }}>
              <h3 style={{ fontSize: "var(--fs-md)", margin: "0 0 6px" }}>{t}</h3>
              <p className="muted" style={{ fontSize: "var(--fs-sm)", margin: 0 }}>{d}</p>
            </div>
          ))}
        </div>
      </section>

      <section id="faq" className="wrap" style={{ padding: "var(--sp-6) 0 var(--sp-6)", maxWidth: 720, marginInline: "auto" }}>
        <h2 style={{ fontSize: "var(--fs-2xl)", textAlign: "center", marginBottom: "var(--sp-5)" }}>Questions</h2>
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--sp-3)" }}>
          {FAQ.map(([q, a]) => (
            <details key={q} className="glass" style={{ padding: "var(--sp-4)" }}>
              <summary style={{ fontFamily: "'Space Grotesk'", fontWeight: 600, cursor: "pointer" }}>{q}</summary>
              <p className="muted" style={{ fontSize: "var(--fs-sm)", margin: "var(--sp-2) 0 0" }}>{a}</p>
            </details>
          ))}
        </div>
      </section>

      <footer className="wrap" style={{ padding: "var(--sp-6) 0", textAlign: "center", color: "var(--text-mut)", fontSize: "var(--fs-xs)" }}>
        <Wordmark size={14} /> — self-hosted, open signup.
      </footer>
    </div>
  );
}
