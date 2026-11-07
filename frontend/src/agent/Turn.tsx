import { useState } from "react";
import { Link } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { jpost } from "@/api/client";
import type { Clip } from "@/api/types";
import { useUsage } from "@/api/usage";
import { useKeptRanges } from "@/editor/RenderPanel";
import { useEditor } from "@/editor/store";
import { fmtDuration } from "@/lib/format";
import { showClip, useAgentChat, type ChatTurn, type Proposal } from "./store";

export const clipScore = (c: Clip) => c.virality ?? c.score;

/** Every clip's index, strongest first. Clips are stored in time order, so the two differ. */
export function clipsByScore(clips: Clip[]): number[] {
  return clips
    .map((c, i) => ({ i, score: clipScore(c) ?? 0 }))
    .sort((a, b) => b.score - a.score)
    .map((c) => c.i);
}

/** One message and whatever it offers. `live` is false once the person has moved past it. */
export function Turn({ turn, live, canUndo, onChoice, onRetry, projectId }: {
  turn: ChatTurn;
  live: boolean;
  canUndo: boolean;
  onChoice: (text: string) => void;
  onRetry: () => void;
  projectId: string | null;
}) {
  const applyOps = useEditor((s) => s.applyAgentOps);
  const update = useAgentChat((s) => s.update);

  if (turn.role === "user") {
    return (
      <div className="turn is-user">
        {turn.file && (
          <span className="video-chip">
            <svg aria-hidden width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round"><path d="M5 5h14a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2zm5 4 5 3-5 3z" /></svg>
            <span className="video-chip-name">{turn.file.name}</span>
          </span>
        )}
        <p>{turn.text}</p>
      </div>
    );
  }

  const offers = (turn.proposals ?? []) as Proposal[];
  const answered = turn.answered || !live;
  return (
    <div className={`turn is-agent${turn.failed ? " is-failed" : ""}`}>
      <span className="turn-avatar" aria-hidden>k</span>
      <div className="turn-body">
        <p>{turn.text}</p>

        {!!turn.changes?.length && (
          <ul className={`agent-changes${turn.undone ? " undone" : ""}`}>
            {turn.changes.map((c) => (
              <li key={c.clip}>
                <span className="turn-change-clip">Clip {c.clip + 1}</span>
                <span>{c.items.join(" · ")}</span>
              </li>
            ))}
          </ul>
        )}
        {turn.undone && <p className="muted turn-small">Undone.</p>}
        {turn.undo && !turn.undone && canUndo && (
          <button
            className="btn ghost small turn-undo"
            onClick={() => {
              applyOps(turn.undo ?? []);
              update(turn.id, { undone: true });
            }}
          >
            Undo
          </button>
        )}

        {!answered && offers.map((p) => {
          if (p.type === "clips") return <ClipsCard key="clips" turnId={turn.id} proposed={p.clips} projectId={projectId} />;
          if (p.type === "render") return <RenderCard key="render" turnId={turn.id} projectId={projectId} />;
          return (
            <div key="choices" className="turn-choices">
              {p.options.map((o, i) => (
                <button
                  key={o}
                  className={`btn small ${i === 0 ? "" : "ghost"}`}
                  onClick={() => {
                    update(turn.id, { answered: true });
                    onChoice(o);
                  }}
                >
                  {o}
                </button>
              ))}
            </div>
          );
        })}

        {turn.retry && !turn.answered && (
          <button
            className="btn ghost small turn-undo"
            onClick={() => {
              update(turn.id, { answered: true });
              onRetry();
            }}
          >
            Try again
          </button>
        )}

        {turn.rendered && projectId && <Link className="btn small turn-undo" to={`/app/p/${projectId}`}>See your clips</Link>}
      </div>
    </div>
  );
}

/** The agent's picks, with the person's choice beside each. Nothing changes until they confirm. */
function ClipsCard({ turnId, proposed, projectId }: { turnId: number; proposed: number[]; projectId: string | null }) {
  const clips = useEditor((s) => s.clips);
  const edits = useEditor((s) => s.edits);
  const activeIdx = useEditor((s) => s.activeIdx);
  const applyOps = useEditor((s) => s.applyAgentOps);
  const add = useAgentChat((s) => s.add);
  const update = useAgentChat((s) => s.update);
  const markDecided = useAgentChat((s) => s.markDecided);
  const [picked, setPicked] = useState(() => new Set(proposed));
  const [showAll, setShowAll] = useState(false);

  const rest = clipsByScore(clips).filter((i) => !proposed.includes(i));
  const shown = showAll ? [...proposed, ...rest] : proposed;
  const count = picked.size;

  function confirm() {
    const ops = clips
      .map((_, i) => ({ clip: i, path: "keep", value: picked.has(i) }))
      .filter((op) => (edits[op.clip]?.keep ?? true) !== op.value);
    const undo = ops.length ? applyOps(ops) : undefined;
    update(turnId, { answered: true });
    if (projectId) markDecided(projectId, true);
    add({ role: "user", text: `Keep ${count === 1 ? "this clip" : `these ${count} clips`}.` });
    // analyses often start clips this way, and an offer that changes nothing is worse than none
    const styled = [...picked].every((i) => edits[i]?.look.vertical && edits[i]?.caption.style === "bold");
    const what = `Kept ${count} and skipped ${clips.length - count}.`;
    add({
      role: "agent",
      text: styled
        ? `${what} ${count === 1 ? "It's" : "They're"} already vertical with bold captions. You can change that, trim in the editor, or render as ${count === 1 ? "it is" : "they are"}.`
        : `${what} You can make ${count === 1 ? "it" : "them"} vertical with bold captions, trim in the editor, or render as ${count === 1 ? "it is" : "they are"}.`,
      undo,
      proposals: [
        { type: "choices", options: [styled ? "Try a different caption style" : `Make ${count === 1 ? "it" : "them"} vertical with bold captions`, "What else should I change?"] },
        { type: "render" },
      ],
    });
  }

  return (
    <div className="decision">
      <span className="eyebrow">Your call</span>
      {shown.map((i) => {
        const c = clips[i];
        if (!c) return null;
        const on = picked.has(i);
        const set = (value: boolean) => {
          const next = new Set(picked);
          if (value) next.add(i);
          else next.delete(i);
          setPicked(next);
        };
        return (
          <div className="decision-clip" key={i}>
            <button
              className={`decision-thumb${activeIdx === i ? " is-showing" : ""}`}
              aria-label={`Preview clip ${i + 1}`}
              aria-pressed={activeIdx === i}
              onClick={() => showClip(i, true)}
            >
              {projectId && (
                <img src={`/media/${projectId}/frame.jpg?t=${Math.floor(c.start_seconds)}`} alt="" loading="lazy" onError={(e) => { e.currentTarget.style.display = "none"; }} />
              )}
              <svg aria-hidden width="20" height="20" viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round"><path d="M8 5v14l11-7z" /></svg>
            </button>
            <div className="decision-text">
              <p className="decision-title">{c.title || c.hook || `Clip ${i + 1}`}</p>
              <p className="muted">
                {`Clip ${i + 1}  ·  ${fmtDuration(c.end_seconds - c.start_seconds)}`}
                {clipScore(c) != null && `  ·  Score ${clipScore(c)}`}
              </p>
              {c.why && <p className="decision-why">{c.why}</p>}
            </div>
            <div className="decision-pick" role="group" aria-label={`Clip ${i + 1}`}>
              <button className={`chip-option${on ? " on" : ""}`} aria-pressed={on} onClick={() => set(true)}>Keep</button>
              <button className={`chip-option${on ? "" : " on"}`} aria-pressed={!on} onClick={() => set(false)}>Skip</button>
            </div>
          </div>
        );
      })}
      <div className="turn-choices">
        <button className="btn small" disabled={count === 0} onClick={confirm}>
          {count === 0 ? "Keep at least one" : `Keep ${count} clip${count === 1 ? "" : "s"}`}
        </button>
        {rest.length > 0 && (
          <button className="btn ghost small" onClick={() => setShowAll(!showAll)}>
            {showAll ? "Show fewer" : `Show all ${clips.length}`}
          </button>
        )}
      </div>
    </div>
  );
}

/** The only way the agent page starts a render: this button, on the same route as the editor's. */
function RenderCard({ turnId, projectId }: { turnId: number; projectId: string | null }) {
  const qc = useQueryClient();
  const ranges = useKeptRanges();
  const setRender = useEditor((s) => s.setRender);
  const rendering = useEditor((s) => s.rendering);
  const update = useAgentChat((s) => s.update);
  const add = useAgentChat((s) => s.add);
  const { data: usage } = useUsage();
  const [starting, setStarting] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const n = ranges.length;
  const left = usage && !usage.unlimited ? usage.renders_today.cap - usage.renders_today.used : null;

  async function start() {
    if (!projectId) return;
    setStarting(true);
    setProblem(null);
    try {
      await jpost(`/api/projects/${projectId}/render`, { ranges });
      update(turnId, { answered: true });
      add({ role: "user", text: `Render ${n} clip${n === 1 ? "" : "s"}.` });
      setRender({ rendering: true, renderProgress: null });
      qc.invalidateQueries({ queryKey: ["usage"] });
    } catch (e) {
      setProblem((e as Error).message || "We couldn't start the render.");
    } finally {
      setStarting(false);
    }
  }

  if (n === 0) return <p className="muted turn-small">Keep at least one clip to render.</p>;
  return (
    <div className="decision">
      <span className="eyebrow">Your call</span>
      <p className="decision-title">Render {n} clip{n === 1 ? "" : "s"} now?</p>
      <p className="muted">
        {usage?.unlimited
          ? "There's no limit on this account. Nothing renders until you say so."
          : `This uses up to ${n} of the ${left ?? "…"} clips you have left today. Nothing renders until you say so.`}
      </p>
      {problem && <p className="dropbox-error" role="alert">{problem}</p>}
      <div className="turn-choices">
        <button className="btn small" disabled={starting || rendering} onClick={start}>
          {starting ? "Starting…" : `Render ${n} clip${n === 1 ? "" : "s"}`}
        </button>
        <button
          className="btn ghost small"
          onClick={() => {
            update(turnId, { answered: true });
            add({ role: "agent", text: "No problem, nothing has rendered. Tell me what you'd like to change, or open the clips in the editor." });
          }}
        >
          Not yet
        </button>
      </div>
    </div>
  );
}
