import { Link } from "react-router-dom";

export type StepState = "done" | "current" | "working" | "todo" | "failed";

export interface WorkflowStep {
  label: string;
  detail: string;
  state: StepState;
  /** opens the editor from this step */
  link?: string;
}

interface Facts {
  uploading: { pct: number; fileName: string } | null;
  project: { id: string; name: string; status: string; jobPct: number } | null;
  clips: number;
  kept: number;
  decided: boolean;
  rendering: { done: number; total: number } | null;
  rendered: number;
}

/** Worked out from the project's real state, never from what the model said. */
export function workflowSteps(f: Facts): WorkflowStep[] {
  const { project } = f;
  const ready = project?.status === "ready" && f.clips > 0;
  const busy = !!project && ["uploading", "queued", "analyzing"].includes(project.status);
  const decided = ready && (f.decided || f.kept < f.clips || f.rendered > 0);
  const editor = project ? `/app/p/${project.id}` : undefined;
  return [
    f.uploading
      ? { label: "Upload", detail: `${f.uploading.pct}%  ·  ${f.uploading.fileName}`, state: "working" }
      : project
        ? { label: "Upload", detail: project.name, state: "done" }
        : { label: "Upload", detail: "Drop in a video, here or on Projects", state: "todo" },
    busy && !f.uploading
      ? { label: "Analyze", detail: project.jobPct ? `${project.jobPct}% done` : "Getting started", state: "working" }
      : project?.status === "failed"
        ? { label: "Analyze", detail: "Stopped. See the chat", state: "failed" }
        : ready
          ? { label: "Analyze", detail: `${f.clips} moment${f.clips === 1 ? "" : "s"} found`, state: "done" }
          : { label: "Analyze", detail: "Uses one minute per minute of video", state: "todo" },
    decided
      ? { label: "Choose clips", detail: `${f.kept} kept, ${f.clips - f.kept} skipped`, state: "done" }
      : ready
        ? { label: "Choose clips", detail: "Waiting on you", state: "current" }
        : { label: "Choose clips", detail: "I'll suggest clips, and you decide", state: "todo" },
    f.rendered > 0
      ? { label: "Edit", detail: "Trim and restyle any time", state: "done" }
      : ready
        ? { label: "Edit", detail: "Optional: trim and style in the editor", state: "todo", link: editor }
        : { label: "Edit", detail: "Trim, captions, framing, colour, sound", state: "todo" },
    f.rendering
      ? { label: "Render", detail: `Rendering ${Math.min(f.rendering.done + 1, f.rendering.total || 1)} of ${f.rendering.total || "…"}`, state: "working" }
      : f.rendered > 0
        ? { label: "Render", detail: `${f.rendered} clip${f.rendered === 1 ? "" : "s"} rendered`, state: "done", link: editor }
        : decided
          ? { label: "Render", detail: "Waiting for your go-ahead", state: "current" }
          : { label: "Render", detail: "Only when you say so", state: "todo" },
  ];
}

export function Workflow({ steps }: { steps: WorkflowStep[] }) {
  return (
    <section className="glass agent-card workflow" aria-label="Workflow">
      <h2>Workflow</h2>
      <ol>
        {steps.map((s, i) => (
          <li key={s.label} className={`wf-step is-${s.state}`}>
            <div className="wf-rail" aria-hidden>
              <span className="wf-marker">
                {s.state === "done" && (
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"><path d="m5 12 5 5 9-10" /></svg>
                )}
                {s.state === "failed" && (
                  <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3.5" strokeLinecap="round"><path d="M6 6l12 12M18 6 6 18" /></svg>
                )}
              </span>
              {i < steps.length - 1 && <span className="wf-line" />}
            </div>
            <div className="wf-text">
              <p className="wf-label">{s.label}</p>
              <p className="wf-detail">{s.detail}</p>
              {s.link && <Link className="wf-link" to={s.link}>Open in the editor</Link>}
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}

/** The same thing on a phone, without taking over the screen. */
export function WorkflowStrip({ steps }: { steps: WorkflowStep[] }) {
  const active = steps.findIndex((s) => ["working", "current", "failed"].includes(s.state));
  const done = steps.every((s) => s.state === "done" || s.label === "Edit") && steps[4].state === "done";
  const label = done ? "All done" : active < 0 ? "Five steps, from upload to render" : `Step ${active + 1} of 5: ${steps[active].label}`;
  return (
    <section className="wf-strip" aria-label="Workflow">
      <div className="wf-strip-top">
        <span>{label}</span>
        {active >= 0 && <span className="muted">{steps[active].detail}</span>}
      </div>
      <div className="wf-strip-bars" aria-hidden>
        {steps.map((s) => <span key={s.label} className={`is-${s.state}`} />)}
      </div>
    </section>
  );
}
