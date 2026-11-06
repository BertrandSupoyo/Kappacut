import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, jget, jpost } from "@/api/client";
import type { Clip, ProjectDetail, ProjectSummary, RenderOutput } from "@/api/types";
import { useUsage } from "@/api/usage";
import { Composer } from "@/agent/Composer";
import { PreviewPanel } from "@/agent/PreviewPanel";
import { historyFrom, showClip, useAgentChat, type AgentReply } from "@/agent/store";
import { clipsByScore, Turn } from "@/agent/Turn";
import { Workflow, WorkflowStrip, workflowSteps } from "@/agent/Workflow";
import { UsageRows } from "@/components/UsageRows";
import { RenderSync } from "@/editor/RenderPanel";
import { flushSave, useEditor } from "@/editor/store";
import { useUploads } from "@/upload/store";
import { useFileDrag } from "@/upload/useFileDrag";

const SUGGESTIONS = [
  "I have a long video. Where do I start?",
  "What suits TikTok better than YouTube Shorts?",
  "How many minutes do I have left?",
  "Which of my projects are ready?",
];
const FALLBACK_MAX_MB = 2048;
const BUSY = ["uploading", "queued", "analyzing"];
// one array, always: a selector that returns a new one each time makes React re-render forever
const NO_CLIPS: Clip[] = [];
const NO_FILES: RenderOutput[] = [];

export function AgentPage() {
  const qc = useQueryClient();
  const { turns, projectId, awaiting, decided, add, setProject, setAwaiting, takeQueuedAsk, takeDraft, reset } = useAgentChat();
  const upload = useUploads();
  const { data: usage } = useUsage();
  const [text, setText] = useState("");
  const [attachment, setAttachment] = useState<File | null>(null);
  const [asking, setAsking] = useState(false);
  const log = useRef<HTMLDivElement>(null);

  const dragging = useFileDrag((file, count) => {
    if (count > 1) add({ role: "agent", text: "One video at a time. Drop just one to start.", failed: true });
    else setAttachment(file);
  });

  const { data: list } = useQuery({
    queryKey: ["projects"],
    queryFn: () => jget<{ projects: ProjectSummary[] }>("/api/projects"),
  });
  const { data: project } = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => jget<ProjectDetail>(`/api/projects/${projectId}`),
    enabled: !!projectId,
    refetchInterval: (q) => (q.state.data && BUSY.includes(q.state.data.status) ? 2500 : false),
  });
  const ready = project?.status === "ready" && !!project.analysis;
  const { data: edit } = useQuery({
    queryKey: ["edit", projectId],
    queryFn: () => jget<{ state: unknown }>(`/api/projects/${projectId}/edit`),
    enabled: ready,
    gcTime: 0,
  });
  const { data: outputs } = useQuery({
    queryKey: ["outputs", projectId],
    queryFn: () => jget<{ clips: RenderOutput[] }>(`/api/projects/${projectId}/outputs`),
    enabled: ready,
  });

  // The editor's store holds the live edits and saves them, here as in the editor. Loaded once per
  // project: loading again from a cached copy would write older edits over newer ones.
  const initEditor = useEditor((s) => s.init);
  const loaded = useRef<string | null>(null);
  useEffect(() => {
    if (!projectId || !project || project.id !== projectId) return;
    if (project.status !== "ready") {
      if (loaded.current === projectId) loaded.current = null;
      return;
    }
    if (!project.analysis || edit === undefined || loaded.current === projectId) return;
    initEditor({
      projectId,
      clips: project.analysis.clips,
      segments: project.analysis.segments,
      duration: project.duration,
      saved: (edit?.state as never) ?? null,
    });
    loaded.current = projectId;
  }, [projectId, project, edit, initEditor]);
  useEffect(() => () => flushSave(), [projectId]);

  const storeReady = useEditor((s) => s.projectId === projectId && s.clips.length > 0);
  const clips = useEditor((s) => (s.projectId === projectId ? s.clips : NO_CLIPS));
  const edits = useEditor((s) => s.edits);
  const rendering = useEditor((s) => s.rendering);
  const renderProgress = useEditor((s) => s.renderProgress);
  const kept = useMemo(() => clips.filter((_, i) => edits[i]?.keep !== false).length, [clips, edits]);

  const ask = useCallback(async (message: string) => {
    setAsking(true);
    try {
      const state = useEditor.getState();
      const chat = useAgentChat.getState();
      const hasEdits = !!chat.projectId && state.projectId === chat.projectId;
      const r = await jpost<AgentReply>("/api/agent", {
        message,
        history: historyFrom(chat.turns.filter((t) => t.text !== message)),
        project_id: chat.projectId,
        open_clip: null,
        edits: hasEdits ? state.edits : {},
      });
      const undo = r.ops.length && hasEdits ? state.applyAgentOps(r.ops) : undefined;
      chat.add({ role: "agent", text: r.reply, changes: r.changes, undo, proposals: r.proposals });
      if (chat.projectId && r.ops.some((op) => op.path === "keep")) chat.markDecided(chat.projectId, true);
      // show the result: a change you can see, or else the clip it's recommending
      if (hasEdits) {
        const changed = r.ops.find((op) => op.path !== "keep")?.clip;
        const suggested = r.proposals.find((p) => p.type === "clips");
        const clip = changed ?? (suggested?.type === "clips" ? suggested.clips[0] : undefined);
        if (clip != null) showClip(clip);
      }
    } catch (e) {
      const signedOut = e instanceof ApiError && e.status === 401;
      const after = useAgentChat.getState();
      // the sign-in page is about to replace this one, and the box goes with it
      if (signedOut) after.setDraft(message);
      after.add({
        role: "agent",
        text: signedOut
          ? "You were signed out, so that didn't go through. Your message is back in the box, ready to send."
          : (e as Error).message || "Something went wrong. Nothing was changed.",
        failed: true,
      });
      setText((cur) => cur || message);        // so it can be sent again without retyping
    } finally {
      setAsking(false);
    }
  }, []);

  // back from signing in again
  useEffect(() => {
    const draft = takeDraft();
    if (draft) setText(draft);
  }, [takeDraft]);

  // typed into the box on the main page
  useEffect(() => {
    const queued = takeQueuedAsk();
    if (queued) {
      add({ role: "user", text: queued });
      void ask(queued);
    }
  }, [add, ask, takeQueuedAsk]);

  // an upload started on the main page carries on here
  useEffect(() => {
    if (upload.phase === "uploading" && upload.projectId && !useAgentChat.getState().projectId) {
      setProject(upload.projectId);
      setAwaiting({ projectId: upload.projectId, ask: null });
    }
  }, [upload.phase, upload.projectId, setProject, setAwaiting]);

  // the analysis has finished: say what came of it
  useEffect(() => {
    if (!awaiting || !project || project.id !== awaiting.projectId) return;
    if (project.status === "failed") {
      setAwaiting(null);
      add({
        role: "agent",
        text: project.error || "The analysis stopped before it finished.",
        retry: project.job?.language ? "language" : "failed",
      });
      return;
    }
    if (project.status !== "ready" || !storeReady) {
      if (project.status === "ready" && project.analysis && project.analysis.clips.length === 0) {
        setAwaiting(null);
        add({ role: "agent", text: "I couldn't find any standalone clips in this one. A longer or livelier video usually works better." });
      }
      return;
    }
    const pending = awaiting.ask;
    setAwaiting(null);
    if (pending) {
      void ask(pending);
    } else {
      const top = clipsByScore(clips).slice(0, 3);
      if (top.length) showClip(top[0]);
      add({
        role: "agent",
        text: `It's ready. I found ${clips.length} moment${clips.length === 1 ? "" : "s"}, and these look strongest, so I'd start here. It's your call.`,
        proposals: [{ type: "clips", clips: top }],
      });
    }
    qc.invalidateQueries({ queryKey: ["usage"] });
  }, [awaiting, project, storeReady, clips, add, ask, qc, setAwaiting]);

  // a render has finished
  const wasRendering = useRef(false);
  useEffect(() => {
    if (wasRendering.current && !rendering && renderProgress) {
      qc.invalidateQueries({ queryKey: ["usage"] });
      if (renderProgress.status === "done") {
        useAgentChat.getState().setPreviewView("files");
        add({ role: "agent", text: renderProgress.error || `Your clip${renderProgress.total === 1 ? " is" : "s are"} ready.`, rendered: true });
      } else if (renderProgress.status === "error") {
        add({ role: "agent", text: renderProgress.error || "The render didn't work. Please try again.", failed: true });
      }
    }
    wasRendering.current = rendering;
  }, [rendering, renderProgress, add, qc]);

  const retry = useMutation({
    mutationFn: (language: boolean) => jpost(`/api/projects/${projectId}/analyze`, language ? { any_language: true } : undefined),
    onSuccess: () => {
      if (projectId) setAwaiting({ projectId, ask: null });
      qc.removeQueries({ queryKey: ["edit", projectId] });
      qc.invalidateQueries({ queryKey: ["project", projectId] });
    },
    onError: (e) => add({ role: "agent", text: (e as Error).message || "We couldn't restart the analysis.", failed: true }),
  });

  async function send(value: string = text) {
    const message = value.trim();
    const file = attachment;
    if ((!message && !file) || asking) return;
    add({ role: "user", text: message || "Can you look at this one?", file: file ? { name: file.name, size: file.size } : undefined });
    setText("");
    setAttachment(null);
    if (!file) {
      await ask(message);
      return;
    }
    // this upload's settings are the chat's own words, so a finished one doesn't get in the way
    if (useUploads.getState().phase === "done") useUploads.getState().dismiss();
    const id = await upload.start(file, { maxBytes: (usage?.max_upload_mb ?? FALLBACK_MAX_MB) * 1024 ** 2, ask: message });
    if (!id) {
      add({ role: "agent", text: useUploads.getState().error || "We couldn't start the upload.", failed: true });
      return;
    }
    setProject(id);
    setAwaiting({ projectId: id, ask: message || null });
    qc.invalidateQueries({ queryKey: ["projects"] });
    qc.invalidateQueries({ queryKey: ["usage"] });
  }

  function pickProject(id: string) {
    setProject(id || null);
    if (!id) return;
    const p = list?.projects.find((x) => x.id === id);
    if (p?.status === "ready" && p.clips > 0) {
      add({
        role: "agent",
        text: `OK, let's work on "${p.name}". It has ${p.clips} clip${p.clips === 1 ? "" : "s"}.`,
        proposals: [{ type: "choices", options: ["Which clips should I post first?", "What should I do next?"] }],
      });
    } else {
      setAwaiting({ projectId: id, ask: null });
    }
  }

  useEffect(() => {
    log.current?.scrollTo({ top: log.current.scrollHeight });
  }, [turns.length, asking, upload.pct, project?.job?.pct, renderProgress?.done]);

  const isThisUpload = upload.phase === "uploading" && (!projectId || upload.projectId === projectId || !upload.projectId);
  const analyzing = !!project && BUSY.includes(project.status) && !isThisUpload;
  const steps = workflowSteps({
    uploading: isThisUpload ? { pct: upload.pct, fileName: upload.fileName } : null,
    project: project && project.id === projectId ? { id: project.id, name: project.name, status: project.status, jobPct: project.job?.pct ?? 0 } : null,
    clips: clips.length,
    kept,
    decided: !!(projectId && decided[projectId]),
    rendering: rendering ? { done: renderProgress?.done ?? 0, total: renderProgress?.total ?? 0 } : null,
    rendered: outputs?.clips.length ?? 0,
  });
  const lastUndo = [...turns].reverse().find((t) => t.undo && !t.undone)?.id;
  const lastAgent = [...turns].reverse().find((t) => t.role === "agent")?.id;
  const started = turns.length > 0 || !!projectId;

  return (
    <div className="agent-page">
      {storeReady && <RenderSync />}
      <div className="agent-page-head">
        <h1>AI agent</h1>
        <select
          className="admin-input admin-select agent-project"
          aria-label="Project to work on"
          value={projectId ?? ""}
          onChange={(e) => pickProject(e.target.value)}
        >
          <option value="">No project</option>
          {(list?.projects ?? []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
        <button
          className="btn ghost compact agent-new"
          onClick={() => {
            reset();
            setText("");
            setAttachment(null);
            loaded.current = null;
          }}
        >
          New chat
        </button>
      </div>

      <WorkflowStrip steps={steps} />

      <div className="agent-layout">
        <section className="glass agent-chat" aria-label="Conversation">
          {!started ? (
            <div className="chat-start">
              <span className="turn-avatar large" aria-hidden>k</span>
              <h2>What are you working on?</h2>
              <p>Drop in a video or ask a question. I'll help you choose what to cut, how to frame it, and what to post first. You make the calls.</p>
              <div className="chat-suggest">
                {SUGGESTIONS.map((s) => <button key={s} className="chip" onClick={() => setText(s)}>{s}</button>)}
              </div>
            </div>
          ) : (
            <div className="chat-log" ref={log} role="log" aria-live="polite">
              {turns.map((t) => (
                <Turn
                  key={t.id}
                  turn={t}
                  live={t.id === lastAgent || !!t.proposals?.length}
                  canUndo={t.id === lastUndo}
                  projectId={projectId}
                  onChoice={(o) => void send(o)}
                  onRetry={(language) => retry.mutate(language)}
                />
              ))}
              {isThisUpload && (
                <Progress label={`Uploading ${upload.fileName}`} pct={upload.pct} />
              )}
              {analyzing && <Progress label={project?.job?.msg || "Getting ready to analyze it"} pct={project?.job?.pct ?? 0} />}
              {rendering && (
                <Progress
                  label={renderProgress?.total ? `Rendering clip ${Math.min(renderProgress.done + 1, renderProgress.total)} of ${renderProgress.total}` : "Starting the render"}
                  pct={renderProgress?.total ? Math.round((renderProgress.done / renderProgress.total) * 100) : 0}
                />
              )}
              {asking && (
                <div className="turn is-agent chat-working" role="status">
                  <span className="spin" aria-hidden /> Thinking…
                </div>
              )}
            </div>
          )}
          <div className={`chat-compose${started ? " has-rule" : ""}`}>
            <Composer
              text={text}
              onText={setText}
              attachment={attachment}
              onAttach={setAttachment}
              onRemove={() => setAttachment(null)}
              onSend={() => void send()}
              dragging={dragging}
              busy={asking}
            />
            <p className="muted chat-note">Kappacut works with English-language videos for now. Only upload videos you own or have permission to use.</p>
          </div>
        </section>

        <div className="agent-side">
          <PreviewPanel
            projectId={projectId}
            project={project}
            storeReady={storeReady}
            uploading={isThisUpload ? { url: upload.localUrl } : null}
            justUploaded={upload.phase === "done" && upload.projectId === projectId}
            files={outputs?.clips ?? NO_FILES}
          />
          <Workflow steps={steps} />
          <section className="glass agent-card" aria-label="What you have left">
            <h2 className="eyebrow">What you have left</h2>
            {usage && <UsageRows usage={usage} show={["minutes", "clips"]} />}
          </section>
        </div>
      </div>
    </div>
  );
}

function Progress({ label, pct }: { label: string; pct: number }) {
  return (
    <div className="chat-progress" role="status">
      <div className="chat-progress-top">
        <span>{label}</span>
        {pct > 0 && <span className="muted">{pct}%</span>}
      </div>
      <div className="meter live" role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}>
        <span style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}
