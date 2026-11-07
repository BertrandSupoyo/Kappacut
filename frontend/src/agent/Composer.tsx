import type { KeyboardEvent } from "react";
import { VIDEO_ACCEPT } from "@/upload/store";

/** Dashed like the drop box, because it takes a dropped video too. */
export function Composer({
  text,
  onText,
  attachment,
  onAttach,
  onRemove,
  onSend,
  dragging,
  busy,
}: {
  text: string;
  onText: (v: string) => void;
  attachment: File | null;
  onAttach: (f: File) => void;
  onRemove: () => void;
  onSend: () => void;
  dragging: boolean;
  busy: boolean;
}) {
  const canSend = !busy && (!!text.trim() || !!attachment);
  function onKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (canSend) onSend();
    }
  }
  return (
    <div className={`composer${dragging ? " is-over" : ""}`}>
      {attachment && (
        <span className="video-chip">
          <span className="video-chip-name">{attachment.name}</span>
          <button className="link-btn" onClick={onRemove}>Remove</button>
        </span>
      )}
      <textarea
        rows={2}
        maxLength={1000}
        aria-label="Ask the AI agent"
        placeholder={dragging ? "Let go to add this video" : "Ask anything, or drop a video here"}
        value={text}
        onChange={(e) => onText(e.target.value)}
        onKeyDown={onKey}
      />
      <div className="composer-bar">
        <label htmlFor="chat-video" className="btn ghost small">Add video</label>
        <input
          id="chat-video"
          className="visually-hidden"
          type="file"
          accept={VIDEO_ACCEPT}
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) onAttach(f);
            e.target.value = "";
          }}
        />
        <button className="composer-send" aria-label="Send" disabled={!canSend} onClick={onSend}>
          <svg aria-hidden width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.25" strokeLinecap="round" strokeLinejoin="round"><path d="M5 12h14m-6-6 6 6-6 6" /></svg>
        </button>
      </div>
    </div>
  );
}
