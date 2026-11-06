import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { useMutation } from "@tanstack/react-query";
import { jpost } from "@/api/client";
import { useEditor, type AgentOp, type AgentTurn } from "./store";

const SUGGESTIONS = {
  open: ["Cut the slow start of this clip", "Make the captions bold, with karaoke", "Follow the speaker's face"],
  overview: ["Make every clip vertical", "Use bold captions on all clips", "Skip the two lowest-scoring clips"],
};
const HISTORY_TURNS = 10;      // app/agent.py takes 12 at most

interface AgentReply {
  reply: string;
  ops: AgentOp[];
  changes: { clip: number; title: string; items: string[] }[];
}

/** What the model is reminded of. An undo is part of the conversation: without it, "do the same
 * for clip 3" could mean the change the person just threw away. A message that failed isn't: left
 * in, it reads as a request still waiting, and the model would carry it out on the next message. */
function historyFrom(chat: AgentTurn[]) {
  return chat
    .filter((t, i) => !t.failed && !(t.role === "user" && chat[i + 1]?.failed))
    .slice(-HISTORY_TURNS)
    .map((t) => ({ role: t.role, text: t.undone ? `${t.text} (The person then undid these changes.)` : t.text }));
}

export function AgentPanel({ openClip, onOpenClip }: { openClip: number | null; onOpenClip: (i: number) => void }) {
  const projectId = useEditor((s) => s.projectId);
  const chat = useEditor((s) => s.agentChat);
  const addTurn = useEditor((s) => s.addAgentTurn);
  const clearChat = useEditor((s) => s.clearAgentChat);
  const applyOps = useEditor((s) => s.applyAgentOps);
  const undoTurn = useEditor((s) => s.undoAgentTurn);
  const [text, setText] = useState("");
  const log = useRef<HTMLDivElement>(null);

  const send = useMutation({
    mutationFn: ({ message }: { message: string; forProject: string }) => {
      const state = useEditor.getState();
      return jpost<AgentReply>(`/api/projects/${state.projectId}/agent`, {
        message,
        history: historyFrom(state.agentChat.slice(0, -1)),
        open_clip: openClip,
        edits: state.edits,
      });
    },
    onSuccess: (r, { forProject }) => {
      // the answer belongs to the project it was asked about, which may not be the one open now
      if (useEditor.getState().projectId !== forProject) return;
      const undo = r.ops.length ? applyOps(r.ops) : undefined;
      addTurn({ role: "assistant", text: r.reply, changes: r.changes, undo });
    },
    onError: (e, { forProject, message }) => {
      if (useEditor.getState().projectId !== forProject) return;
      addTurn({ role: "assistant", text: (e as Error).message || "Something went wrong. Nothing was changed.", failed: true });
      setText((cur) => cur || message);            // so it can be sent again without retyping
    },
  });

  useEffect(() => {
    log.current?.scrollTo({ top: log.current.scrollHeight });
  }, [chat.length, send.isPending]);

  function submit(value = text) {
    const message = value.trim();
    if (!message || send.isPending) return;
    addTurn({ role: "user", text: message });
    setText("");
    send.mutate({ message, forProject: projectId });
  }

  function onKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  }

  const lastAnswer = chat.length - 1;
  return (
    <section className="glass agent" aria-label="AI editor">
      <div className="agent-head">
        <h2>AI editor</h2>
        {chat.length > 0 && <button className="link-btn" disabled={send.isPending} onClick={clearChat}>Clear</button>}
      </div>

      {chat.length === 0 ? (
        <>
          <p className="muted agent-help">
            Say what you'd like changed. It can trim, skip, and change captions, framing, colour and sound. It reads the
            transcript but can't see the video, and it never renders anything.
          </p>
          <div className="agent-suggest">
            {(openClip === null ? SUGGESTIONS.overview : SUGGESTIONS.open).map((s) => (
              <button key={s} className="chip" onClick={() => setText(s)}>{s}</button>
            ))}
          </div>
        </>
      ) : (
        <div className="agent-log" ref={log} role="log" aria-live="polite">
          {chat.map((turn, i) => (
            <div key={turn.id} className={`agent-turn ${turn.role}${turn.failed ? " failed" : ""}`}>
              <p>{turn.text}</p>
              {!!turn.changes?.length && (
                <ul className={`agent-changes${turn.undone ? " undone" : ""}`}>
                  {turn.changes.map((c) => (
                    <li key={c.clip}>
                      <button className="link-btn" onClick={() => onOpenClip(c.clip)}>Clip {c.clip + 1}</button>
                      <span>{c.items.join(" · ")}</span>
                    </li>
                  ))}
                </ul>
              )}
              {turn.undone && <p className="muted agent-undone">Undone.</p>}
              {turn.undo && !turn.undone && i === lastAnswer && (
                <button className="btn ghost small" disabled={send.isPending} onClick={() => undoTurn(turn.id)}>Undo</button>
              )}
            </div>
          ))}
          {send.isPending && (
            <div className="agent-turn assistant agent-working" role="status">
              <span className="spin" aria-hidden /> Working on it…
            </div>
          )}
        </div>
      )}

      <div className="agent-input">
        <textarea
          rows={2}
          maxLength={1000}
          aria-label="Tell the AI editor what to change"
          placeholder={openClip === null ? "e.g. make every clip vertical" : "e.g. end this clip after the punchline"}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKey}
        />
        <button className="btn small" disabled={!text.trim() || send.isPending} onClick={() => submit()}>Send</button>
      </div>
    </section>
  );
}
