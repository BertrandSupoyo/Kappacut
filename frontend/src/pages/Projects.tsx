import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { jdelete, jget, jpost } from "@/api/client";
import type { ProjectSummary } from "@/api/types";
import { useUsage } from "@/api/usage";
import { useAgentChat } from "@/agent/store";
import { UsageRows } from "@/components/UsageRows";
import { useToast } from "@/components/Toast";
import { fmtAgo, fmtDuration } from "@/lib/format";
import { ClipSettingsDrawer } from "@/upload/ClipSettingsDrawer";
import { DropBox } from "@/upload/DropBox";
import { useUploads } from "@/upload/store";
import { useFileDrag } from "@/upload/useFileDrag";

const STATUS_LABEL: Record<string, string> = {
  uploading: "Uploading",
  queued: "Queued",
  analyzing: "Analyzing",
  ready: "Ready",
  failed: "Failed",
};

const FALLBACK_MAX_MB = 2048;

export function Projects() {
  const qc = useQueryClient();
  const toast = useToast();
  const phase = useUploads((s) => s.phase);
  const start = useUploads((s) => s.start);
  const { data: usage } = useUsage();
  const [settingsOpen, setSettingsOpen] = useState(false);

  const dragging = useFileDrag((file, count) => {
    if (count > 1) {
      useUploads.setState({ error: "One video at a time. Drop just one to start." });
      return;
    }
    void start(file, { maxBytes: (usage?.max_upload_mb ?? FALLBACK_MAX_MB) * 1024 ** 2 });
  }, phase !== "uploading");

  const { data, isLoading } = useQuery({
    queryKey: ["projects"],
    queryFn: () => jget<{ projects: ProjectSummary[] }>("/api/projects"),
    refetchInterval: (q) => {
      const busy = q.state.data?.projects.some((p) => ["uploading", "queued", "analyzing"].includes(p.status));
      return busy ? 3000 : false;
    },
  });

  // a new project appears, and its limits change, when an upload starts or ends
  useEffect(() => {
    qc.invalidateQueries({ queryKey: ["projects"] });
    qc.invalidateQueries({ queryKey: ["usage"] });
  }, [phase, qc]);

  const del = useMutation({
    mutationFn: (id: string) => jdelete(`/api/projects/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["projects"] });
      qc.invalidateQueries({ queryKey: ["usage"] });
    },
  });

  const retry = useMutation({
    mutationFn: (id: string) => jpost(`/api/projects/${id}/analyze`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["projects"] }),
    onError: (e) => toast((e as Error).message || "Couldn't restart the analysis.", "error"),
  });

  const projects = data?.projects ?? [];

  return (
    <div className="home">
      <div className="home-hero">
        <DropBox dragging={dragging} onOpenSettings={() => setSettingsOpen(true)} />
        <div className={`home-side${dragging ? " is-dimmed" : ""}`}>
          <AskCard />
          <section className="home-card" aria-label="This month">
            <h2 className="eyebrow">This month</h2>
            {usage && <UsageRows usage={usage} show={["minutes", "storage", "projects"]} />}
          </section>
        </div>
      </div>

      <section className={`home-projects${dragging ? " is-dimmed" : ""}`} aria-labelledby="your-projects">
        <div className="home-projects-head">
          <h2 id="your-projects">Your projects</h2>
          <span className="muted">{projects.length}</span>
        </div>
        {isLoading ? (
          <div className="glass" style={{ padding: "var(--sp-6)", textAlign: "center" }}>
            <div className="spin" style={{ margin: "0 auto" }} />
          </div>
        ) : projects.length === 0 ? (
          <p className="muted home-empty">No projects yet. Upload a video and Kappacut hands you a ranked shortlist of clips.</p>
        ) : (
          <div className="project-grid">
            {projects.map((p) => (
              <article key={p.id} className="glass project-card">
                <div className="project-thumb">
                  {p.duration ? (
                    <img
                      src={`/media/${p.id}/frame.jpg?t=${Math.floor(p.duration / 3)}`}
                      alt=""
                      loading="lazy"
                      onError={(e) => { e.currentTarget.style.display = "none"; }}
                    />
                  ) : null}
                  {p.duration ? <span className="badge" style={{ position: "absolute", right: 8, bottom: 8 }}>{fmtDuration(p.duration)}</span> : null}
                  <span className="project-status">
                    <StatusDot status={p.status} />
                    {STATUS_LABEL[p.status]}
                  </span>
                  <button
                    className="project-delete"
                    aria-label={`Delete ${p.name}`}
                    title="Delete project"
                    onClick={() => confirm(`Delete "${p.name}"? Its clips and renders go too.`) && del.mutate(p.id)}
                  >
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                      <path d="M4 7h16M10 11v6M14 11v6M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4h6v3" />
                    </svg>
                  </button>
                </div>
                <div className="project-body">
                  {/* the title link stretches over the whole card (see .project-card a::after) */}
                  <Link to={`/app/p/${p.id}`} className="project-title clamp-2">{p.name}</Link>
                  <div className="muted" style={{ fontSize: "var(--fs-xs)" }}>
                    {p.clips} clip{p.clips === 1 ? "" : "s"}
                    {p.rendered > 0 && ` · ${p.rendered} rendered`}
                    {p.has_edits && " · edited"}
                    {" · "}{fmtAgo(p.last_opened_at || p.created_at)}
                  </div>
                  {p.status === "failed" && (
                    <button
                      className="btn ghost small project-retry"
                      style={{ alignSelf: "flex-start" }}
                      disabled={retry.isPending}
                      onClick={() => retry.mutate(p.id)}
                    >
                      Retry analysis
                    </button>
                  )}
                </div>
              </article>
            ))}
          </div>
        )}
      </section>

      <ClipSettingsDrawer open={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </div>
  );
}

function AskCard() {
  const nav = useNavigate();
  const queueAsk = useAgentChat((s) => s.queueAsk);
  const [text, setText] = useState("");
  function submit(e: FormEvent) {
    e.preventDefault();
    if (text.trim()) queueAsk(text.trim());
    nav("/app/agent");
  }
  return (
    <section className="home-card home-ask" aria-labelledby="ask-agent">
      <div>
        <h2 id="ask-agent" className="home-card-title">Ask the AI agent</h2>
        <p className="muted">Not sure what to do with a video? Ask first, or upload it in the chat.</p>
      </div>
      <form className="home-ask-field" onSubmit={submit}>
        <input aria-label="Ask the AI agent" maxLength={1000} placeholder="Ask anything…" value={text} onChange={(e) => setText(e.target.value)} />
        <button aria-label="Open the AI agent">
          <svg aria-hidden width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M5 12h14m-6-6 6 6-6 6" /></svg>
        </button>
      </form>
    </section>
  );
}

function StatusDot({ status }: { status: string }) {
  const color =
    status === "ready" ? "var(--good)" : status === "failed" ? "var(--danger)" : status === "analyzing" ? "var(--cyan)" : "var(--amber)";
  const pulse = ["uploading", "queued", "analyzing"].includes(status);
  return (
    <span
      style={{
        width: 7,
        height: 7,
        borderRadius: "50%",
        background: color,
        boxShadow: pulse ? `0 0 0 0 ${color}` : "none",
        animation: pulse ? "dot 1.4s infinite" : "none",
      }}
    />
  );
}
