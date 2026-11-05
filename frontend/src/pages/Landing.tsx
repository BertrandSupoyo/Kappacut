import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { usePublicConfig } from "@/api/config";
import { useAuth } from "@/auth/AuthContext";
import { SiteFooter } from "@/components/SiteFooter";
import { Wordmark } from "@/components/Wordmark";

const icon = (d: ReactNode) => (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    {d}
  </svg>
);

const STEPS = [
  { n: "01", i: icon(<><path d="M12 16V4M7 9l5-5 5 5" /><path d="M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3" /></>), t: "Upload a video", d: "A stream VOD, a compilation, an interview — up to 2 GB. Resumable, so a dropped connection doesn't cost you the upload." },
  { n: "02", i: icon(<><path d="M4 20V10M10 20V4M16 20v-7M22 20H2" /></>), t: "Get ranked highlights", d: "Every line transcribed, garbled audio filtered out, an LLM scores the standalone moments and tells you why each one lands." },
  { n: "03", i: icon(<><circle cx="6" cy="6" r="3" /><circle cx="6" cy="18" r="3" /><path d="M20 4 8.1 15.9M14.5 14.5 20 20M8.1 8.1 12 12" /></>), t: "Trim & render", d: "Adjust the in/out points, edit the auto-captions, add a 9:16 reframe or sound effects, then render. Captions burned in." },
];

const FEATURES = [
  { i: icon(<><path d="M4 6h16M4 12h10M4 18h7" /></>), t: "Transcript-first picking", d: "Whisper transcribes the whole thing; the picker works from the words, not guesses. Repetitive hallucinations and dead air are dropped before scoring." },
  { i: icon(<><path d="M12 3l2.6 5.6 6.1.7-4.5 4.2 1.2 6L12 16.6 6.6 19.5l1.2-6-4.5-4.2 6.1-.7z" /></>), t: "A reason for every clip", d: "Each pick comes with its hook, its payoff line, a 0–100 score and a one-line why — so you can skim the shortlist instead of watching all of it." },
  { i: icon(<><rect x="7" y="2" width="10" height="20" rx="2" /><path d="M10 17h4" /></>), t: "9:16 that follows the face", d: "Reframe to vertical with a face-tracking crop or a blurred fill, pick a caption style and colour, drop in whooshes and impacts." },
  { i: icon(<><rect x="4" y="11" width="16" height="10" rx="2" /><path d="M8 11V7a4 4 0 0 1 8 0v4" /></>), t: "Your library stays yours", d: "Projects, analyses and rendered clips stay private to your account, and you can download or delete all of it at any time." },
];

const FAQ = [
  ["Is it free?", "Yes. The free plan covers 60 minutes of transcription a month, 2 GB per file, and keeps your projects for about a month of inactivity."],
  ["Which languages does it work with?", "English, for now. If a video sounds like it's in another language, Kappacut stops before using any of your minutes and asks whether you want to analyze it anyway. The clips and captions are likely to be poor if you do."],
  ["Where does my video go?", "It's stored privately in your account. The audio goes to Groq for transcription, and the transcript goes to the model that picks moments. The Privacy Policy lists everyone who handles your data."],
  ["How long does a video take?", "A few minutes for a typical stream VOD — most of it is transcription. You watch the progress live."],
  ["Can I bring my own clips to caption?", "Right now Kappacut starts from one uploaded source per project. Multi-source stitching isn't in yet."],
];

/* the hero's product picture: a long source waveform with three moments lifted out of it as
   vertical clips — drawn in CSS so it costs nothing to load and stays crisp at any size */
const WAVE = Array.from({ length: 96 }, (_, i) => 18 + Math.round(70 * Math.abs(Math.sin(i * 0.41) * Math.cos(i * 0.13))));
const PICKS = [
  { at: 14, w: 7, cap: ["WAIT FOR", "IT"], title: "The bomb drop", score: 92, len: "0:22", tone: "a" },
  { at: 47, w: 6, cap: ["THAT'S", "INSANE"], title: "Fat cracked like ice", score: 88, len: "0:16", tone: "b" },
  { at: 78, w: 8, cap: ["ONE MORE", "BITE"], title: "Counting the drops", score: 83, len: "0:19", tone: "c" },
];

function HeroVisual() {
  return (
    <div className="hero-visual" aria-hidden>
      <div className="hv-clips">
        {PICKS.map((p, i) => (
          <div key={p.title} className={`hv-clip hv-${p.tone}`} style={{ ["--i" as string]: i }}>
            <span className="hv-len">{p.len}</span>
            <span className="hv-score">{p.score}</span>
            <span className="hv-face" />
            <span className="hv-cap">
              {p.cap[0]} <em>{p.cap[1]}</em>
            </span>
            <span className="hv-title">{p.title}</span>
          </div>
        ))}
      </div>
      <div className="hv-source">
        <div className="hv-wave">
          {WAVE.map((h, i) => (
            <i key={i} style={{ height: `${h}%` }} className={PICKS.some((p) => i >= p.at && i < p.at + p.w) ? "on" : undefined} />
          ))}
        </div>
        {PICKS.map((p) => (
          <span key={p.at} className="hv-pick" style={{ left: `${(p.at / WAVE.length) * 100}%`, width: `${(p.w / WAVE.length) * 100}%` }} />
        ))}
        <span className="hv-source-label">source · 2:41:08</span>
      </div>
    </div>
  );
}

export function Landing() {
  const { user } = useAuth();
  const cfg = usePublicConfig();
  const appHref = user ? "/app" : "/register";
  const cta = user ? "Open the app" : cfg?.invite_only ? "Sign up with your invite" : "Get started — it's free";

  return (
    <div className="landing">
      <header className="wrap" style={{ display: "flex", alignItems: "center", gap: "var(--sp-5)", padding: "var(--sp-4) 0" }}>
        <Wordmark />
        <nav className="landing-nav">
          <a href="#how" className="muted hide-sm">How it works</a>
          <a href="#features" className="muted hide-sm">Features</a>
          <a href="#faq" className="muted hide-sm">FAQ</a>
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

      <section className="wrap hero">
        <span className="eyebrow-pill"><span className="live-dot" /> AI clip finder for long videos</span>
        <h1 className="hero-title">
          <span className="hero-dim">Stop scrubbing.</span><br />
          Start clipping.
        </h1>
        <p className="hero-sub">
          Kappacut transcribes an entire VOD or compilation, ranks the moments that stand on their own,
          and hands you a shortlist you can trim and render into short-form clips.
        </p>
        <div style={{ display: "flex", gap: "var(--sp-3)", justifyContent: "center", flexWrap: "wrap" }}>
          <Link className="btn btn-lg" to={appHref}>{cta}</Link>
          <a className="btn ghost btn-lg" href="#how">How it works</a>
        </div>
        <p className="muted" style={{ fontSize: "var(--fs-xs)", marginTop: "var(--sp-4)" }}>
          {cfg?.invite_only && "Private test, invite only · "}Free plan · 60 min/month · no card · English-language videos
        </p>
        <HeroVisual />
      </section>

      <section id="how" className="wrap landing-section">
        <span className="eyebrow section-eyebrow">How it works</span>
        <h2 className="section-title">Three steps, one pass</h2>
        <div className="steps">
          {STEPS.map((s) => (
            <div key={s.n} className="glass step">
              <div className="step-head">
                <span className="icon-tile">{s.i}</span>
                <span className="step-n">{s.n}</span>
              </div>
              <h3>{s.t}</h3>
              <p className="muted">{s.d}</p>
            </div>
          ))}
        </div>
      </section>

      <section id="features" className="wrap landing-section">
        <span className="eyebrow section-eyebrow">Features</span>
        <h2 className="section-title">What's in the box</h2>
        <div className="features">
          {FEATURES.map((f) => (
            <div key={f.t} className="glass feature">
              <span className="icon-tile">{f.i}</span>
              <div>
                <h3>{f.t}</h3>
                <p className="muted">{f.d}</p>
              </div>
            </div>
          ))}
        </div>
      </section>

      <section id="faq" className="wrap landing-section" style={{ maxWidth: 720 }}>
        <span className="eyebrow section-eyebrow">FAQ</span>
        <h2 className="section-title">Questions</h2>
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--sp-3)" }}>
          {FAQ.map(([q, a]) => (
            <details key={q} className="glass faq">
              <summary>{q}</summary>
              <p className="muted">{a}</p>
            </details>
          ))}
        </div>
      </section>

      <section className="wrap landing-section">
        <div className="glass cta-band">
          <h2>Your next ten clips are already in that VOD.</h2>
          <p className="muted">Upload it and get a ranked shortlist in a few minutes.</p>
          <Link className="btn btn-lg" to={appHref}>{cta}</Link>
        </div>
      </section>

      <div style={{ paddingTop: "var(--sp-5)" }}>
        <SiteFooter />
      </div>
    </div>
  );
}
