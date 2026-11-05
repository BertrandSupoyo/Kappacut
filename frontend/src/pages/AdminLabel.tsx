import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, jget } from "@/api/client";
import { useToast } from "@/components/Toast";
import { fmtDuration } from "@/lib/format";
import { AdminOnly } from "./Admin";
import { pct, ScoreTiles, type Score } from "./AdminTraining";

interface Moment {
  start: number;
  end: number;
  note: string;
}

interface Detail {
  id: string;
  name: string;
  seconds: number | null;
  category: string | null;
  status: "none" | "draft" | "done" | "stale";
  moments: Moment[];
  blind: boolean;
  revealed: boolean;
  comparison: {
    stats: Score;
    picks: { start: number; end: number; title: string; score: number; good: boolean }[];
    moments: (Moment & { found: boolean })[];
  } | null;
}

const MIN_SECONDS = 1;
const SLACK = 1;

/** "3:25", "1:03:25" or "205.5" to seconds; null if it isn't a time. */
function parseClock(text: string): number | null {
  const parts = text.trim().split(":");
  if (parts.length > 3 || parts.some((p) => !/^\d+(\.\d+)?$/.test(p))) return null;
  return parts.reduce((t, p) => t * 60 + Number(p), 0);
}

/** The same checks as app/training.py clean_moments, so a bad edit explains itself at once. */
function problemWith(moments: Moment[], duration: number | null): string | null {
  const sorted = [...moments].sort((a, b) => a.start - b.start);
  for (const m of sorted) {
    if (m.end - m.start < MIN_SECONDS) return `The moment at ${fmtDuration(m.start)} must be at least ${MIN_SECONDS} second long.`;
    if (duration && m.end > duration + SLACK) return `The moment at ${fmtDuration(m.start)} ends after the video does.`;
  }
  for (let i = 1; i < sorted.length; i++) {
    if (sorted[i].start < sorted[i - 1].end) {
      return `The moments at ${fmtDuration(sorted[i - 1].start)} and ${fmtDuration(sorted[i].start)} overlap. Merge them or move one.`;
    }
  }
  return null;
}

export function AdminLabel() {
  const { id } = useParams<{ id: string }>();
  const { data, error } = useQuery({
    queryKey: ["admin", "training", "video", id],
    queryFn: () => jget<Detail>(`/api/admin/training/videos/${id}`),
    refetchOnWindowFocus: false,   // the page holds its own edits; a refetch must not reset them
  });
  return (
    <AdminOnly>
      <div className="training" style={{ display: "flex", flexDirection: "column", gap: "var(--sp-4)", margin: "var(--sp-4) 0" }}>
        <Link to="/admin/training" className="muted" style={{ fontSize: "var(--fs-sm)" }}>← Training</Link>
        {error ? (
          <p className="err">{(error as Error).message}</p>
        ) : !data ? (
          <p className="muted">Loading…</p>
        ) : (
          <Workspace key={data.id} initial={data} />
        )}
      </div>
    </AdminOnly>
  );
}

type Segment = { start: number; end: number; label: string; hollow?: boolean };

function Timeline({ duration, time, pending, rows, onSeek }: {
  duration: number;
  time: number;
  pending: number | null;
  rows: { name: string; color: string; segments: Segment[] }[];
  onSeek: (t: number) => void;
}) {
  const at = (t: number) => `${Math.min(100, (t / duration) * 100)}%`;
  return (
    <div className="tl">
      {rows.map((r) => (
        <div className="tl-row" key={r.name}>
          <span className="tl-name">{r.name}</span>
          <div
            className="tl-track"
            onClick={(e) => {
              const box = e.currentTarget.getBoundingClientRect();
              onSeek(((e.clientX - box.left) / box.width) * duration);
            }}
          >
            {r.segments.map((s) => (
              <button
                key={s.start}
                type="button"
                className={`tl-seg${s.hollow ? " hollow" : ""}`}
                style={{ left: at(s.start), width: `${((s.end - s.start) / duration) * 100}%`, "--seg": r.color } as React.CSSProperties}
                title={s.label}
                aria-label={`Jump to ${s.label}`}
                onClick={(e) => {
                  e.stopPropagation();
                  onSeek(s.start);
                }}
              />
            ))}
            {pending !== null && <span className="tl-pending" style={{ left: at(pending), width: `calc(${at(Math.max(time, pending))} - ${at(pending)})` }} />}
            <span className="tl-head" style={{ left: at(time) }} />
          </div>
        </div>
      ))}
      <div className="tl-row">
        <span />
        <div className="tl-scale muted"><span>0:00</span><span>{fmtDuration(duration)}</span></div>
      </div>
    </div>
  );
}

function TimeField({ value, label, onChange, disabled }: { value: number; label: string; onChange: (t: number) => void; disabled?: boolean }) {
  const [text, setText] = useState(fmtDuration(value));
  useEffect(() => setText(fmtDuration(value)), [value]);
  const commit = () => {
    const t = parseClock(text);
    if (t === null) setText(fmtDuration(value));
    else if (fmtDuration(t) !== fmtDuration(value)) onChange(t);
  };
  return (
    <input
      className="admin-input time-input"
      aria-label={label}
      value={text}
      disabled={disabled}
      onChange={(e) => setText(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()}
    />
  );
}

function Workspace({ initial }: { initial: Detail }) {
  const qc = useQueryClient();
  const toast = useToast();
  const path = `/api/admin/training/videos/${initial.id}/label`;
  const [view, setView] = useState(initial);
  const [moments, setMoments] = useState(initial.moments);
  const [pending, setPending] = useState<number | null>(null);
  const [time, setTime] = useState(0);
  const [blind, setBlind] = useState(false);
  const [saveState, setSaveState] = useState<"saved" | "saving" | "error">("saved");
  const videoRef = useRef<HTMLVideoElement>(null);
  const dirty = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const latest = useRef(moments);
  latest.current = moments;
  // one request at a time, in order: a slow autosave must not land after Finish and reopen the label
  const queue = useRef<Promise<unknown>>(Promise.resolve());

  const done = view.status === "done";
  const duration = view.seconds ?? 0;
  const issue = problemWith(moments, view.seconds);

  const send = useCallback((body: { moments: Moment[]; done: boolean; blind: boolean }) => {
    const run = queue.current.then(() => api<Detail>(path, { method: "PUT", body: JSON.stringify(body) }));
    queue.current = run.catch(() => undefined);
    return run;
  }, [path]);

  const refresh = useCallback(() => qc.invalidateQueries({ queryKey: ["admin", "training"] }), [qc]);

  // autosave a draft: an hour of marking must survive a closed tab
  useEffect(() => {
    if (!dirty.current || done) return;
    if (issue) {
      setSaveState("error");
      return;
    }
    setSaveState("saving");
    timer.current = setTimeout(async () => {
      dirty.current = false;
      try {
        const v = await send({ moments: latest.current, done: false, blind: false });
        setView((old) => ({ ...v, moments: old.moments }));
        setSaveState("saved");
        refresh();
      } catch (e) {
        dirty.current = true;
        setSaveState("error");
        toast((e as Error).message || "Couldn't save your moments.", "error");
      }
    }, 800);
    return () => clearTimeout(timer.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [moments]);

  // leaving inside the 800 ms window: send what's there
  useEffect(() => () => {
    if (dirty.current && !problemWith(latest.current, initial.seconds)) {
      clearTimeout(timer.current);
      void send({ moments: latest.current, done: false, blind: false }).catch(() => undefined).then(refresh);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const edit = (next: Moment[]) => {
    dirty.current = true;
    setMoments(next);
  };
  const seek = (t: number) => {
    if (videoRef.current) videoRef.current.currentTime = t;
    setTime(t);
  };

  const markStart = useCallback(() => setPending(videoRef.current?.currentTime ?? 0), []);
  const markEnd = useCallback(() => {
    const now = videoRef.current?.currentTime ?? 0;
    if (pending === null) return;
    if (now - pending < MIN_SECONDS) {
      toast(`Play at least ${MIN_SECONDS} second past the start first.`, "error");
      return;
    }
    dirty.current = true;
    setMoments((m) => [...m, { start: pending, end: now, note: "" }].sort((a, b) => a.start - b.start));
    setPending(null);
  }, [pending, toast]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (done || e.ctrlKey || e.metaKey || e.altKey) return;
      if ((e.target as HTMLElement).matches?.("input, textarea, select")) return;
      if (e.key.toLowerCase() === "i") markStart();
      if (e.key.toLowerCase() === "o") markEnd();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [done, markStart, markEnd]);

  const finish = async () => {
    if (!moments.length && !confirm("Finish with no moments? That says nothing in this video is worth posting.")) return;
    clearTimeout(timer.current);
    dirty.current = false;
    setSaveState("saving");
    try {
      const v = await send({ moments, done: true, blind });
      setView(v);
      setMoments(v.moments);
      setPending(null);
      setSaveState("saved");
      refresh();
    } catch (e) {
      dirty.current = true;
      setSaveState("error");
      toast((e as Error).message || "Couldn't finish this label.", "error");
    }
  };

  const reopen = async () => {
    if (view.blind && !confirm("You've now seen the AI's picks, so this video will stop counting as blind. Edit anyway?")) return;
    try {
      const v = await send({ moments, done: false, blind: false });
      setView(v);
      setBlind(false);
      refresh();
    } catch (e) {
      toast((e as Error).message || "Couldn't reopen this label.", "error");
    }
  };

  const c = view.comparison;
  const rows = [
    { name: "You", color: "var(--viz-you)", segments: moments.map((m) => ({ start: m.start, end: m.end, label: `${fmtDuration(m.start)}–${fmtDuration(m.end)}${m.note ? `: ${m.note}` : ""}` })) },
    ...(c ? [{
      name: "AI",
      color: "var(--viz-ai)",
      segments: c.picks.map((p) => ({ start: p.start, end: p.end, hollow: !p.good, label: `${fmtDuration(p.start)}–${fmtDuration(p.end)}: ${p.title || "AI pick"}` })),
    }] : []),
  ];
  const missed = c ? c.moments.filter((m) => !m.found) : [];

  return (
    <>
      <div>
        <h1 style={{ fontSize: "var(--fs-xl)" }}>{view.name}</h1>
        <p className="muted" style={{ margin: "var(--sp-1) 0 0", fontSize: "var(--fs-sm)" }}>
          {done
            ? `Finished · ${view.blind ? "marked before seeing the AI's picks" : "not blind"}`
            : "Mark every moment you'd post. The AI's picks stay hidden until you finish."}
        </p>
      </div>
      {view.status === "stale" && (
        <p className="err" style={{ margin: 0 }}>
          This video was replaced after you labelled it, so these times may point at the wrong place. Check them; this label isn't scored until you finish it again.
        </p>
      )}
      <div className="label-grid">
        <section className="glass" style={{ padding: "var(--sp-3)", display: "flex", flexDirection: "column", gap: "var(--sp-3)" }} aria-label="Video">
          <video
            ref={videoRef}
            src={`/media/${view.id}/source`}
            controls
            preload="metadata"
            className="label-video"
            onTimeUpdate={(e) => setTime(e.currentTarget.currentTime)}
            onSeeked={(e) => setTime(e.currentTarget.currentTime)}
          />
          {duration > 0 && <Timeline duration={duration} time={time} pending={pending} rows={rows} onSeek={seek} />}
          {c && (
            <p className="muted" style={{ margin: 0, fontSize: "var(--fs-xs)" }}>
              AI row: filled is on a moment you marked, outlined isn't. Select any block to jump to it.
            </p>
          )}
          {!done && (
            <div style={{ display: "flex", alignItems: "center", gap: "var(--sp-2)", flexWrap: "wrap" }}>
              <button className="btn ghost" onClick={markStart}>{pending === null ? "Start here (I)" : "Restart here (I)"}</button>
              <button className="btn" disabled={pending === null} onClick={markEnd}>End here (O)</button>
              {pending !== null && (
                <>
                  <span className="muted" style={{ fontSize: "var(--fs-sm)" }}>Started at {fmtDuration(pending)}</span>
                  <button className="btn ghost" style={{ padding: "4px 10px" }} onClick={() => setPending(null)}>Cancel</button>
                </>
              )}
            </div>
          )}
        </section>

        <section className="glass" style={{ padding: "var(--sp-3)" }} aria-labelledby="h-moments">
          <div style={{ display: "flex", alignItems: "baseline", gap: "var(--sp-2)", marginBottom: "var(--sp-2)" }}>
            <h2 id="h-moments" style={{ fontSize: "var(--fs-md)" }}>Your moments ({moments.length})</h2>
            {!done && (
              <span className="muted" style={{ marginLeft: "auto", fontSize: "var(--fs-xs)" }} aria-live="polite">
                {saveState === "saving" ? "Saving…" : saveState === "error" ? "Not saved" : "Saved"}
              </span>
            )}
          </div>
          {moments.length === 0 ? (
            <p className="muted" style={{ margin: 0, fontSize: "var(--fs-sm)" }}>
              Play the video, press <b>Start here</b> when a moment begins and <b>End here</b> when it ends.
            </p>
          ) : (
            <ul className="moment-list">
              {moments.map((m, i) => (
                <li key={`${i}-${m.start}`} className="moment-row">
                  <button className="btn ghost moment-jump" aria-label={`Jump to ${fmtDuration(m.start)}`} title="Jump here" onClick={() => seek(m.start)}>▶</button>
                  <TimeField
                    label={`Start of moment ${i + 1}`}
                    value={m.start}
                    disabled={done}
                    onChange={(t) => edit(moments.map((x, j) => (j === i ? { ...x, start: t } : x)).sort((a, b) => a.start - b.start))}
                  />
                  <TimeField
                    label={`End of moment ${i + 1}`}
                    value={m.end}
                    disabled={done}
                    onChange={(t) => edit(moments.map((x, j) => (j === i ? { ...x, end: t } : x)))}
                  />
                  <input
                    className="admin-input moment-note"
                    aria-label={`Note for moment ${i + 1}`}
                    placeholder="Note (optional)"
                    maxLength={300}
                    value={m.note}
                    disabled={done}
                    onChange={(e) => edit(moments.map((x, j) => (j === i ? { ...x, note: e.target.value } : x)))}
                  />
                  {!done && (
                    <button className="btn danger moment-jump" aria-label={`Remove moment ${i + 1}`} title="Remove" onClick={() => edit(moments.filter((_, j) => j !== i))}>×</button>
                  )}
                </li>
              ))}
            </ul>
          )}
          {issue && !done && <p className="err" style={{ margin: "var(--sp-2) 0 0" }} role="alert">{issue}</p>}
          {!done ? (
            <div style={{ marginTop: "var(--sp-3)", paddingTop: "var(--sp-3)", borderTop: "1px solid var(--border)", display: "flex", flexDirection: "column", gap: "var(--sp-2)" }}>
              <label style={{ display: "flex", gap: "var(--sp-2)", fontSize: "var(--fs-sm)", alignItems: "flex-start" }}>
                <input type="checkbox" checked={blind && !view.revealed} disabled={view.revealed} onChange={(e) => setBlind(e.target.checked)} />
                <span className={view.revealed ? "muted" : undefined}>
                  {view.revealed
                    ? "You've already seen the AI's picks here, so this video won't count as blind."
                    : "I marked these before seeing the AI's clips for this video"}
                </span>
              </label>
              <button className="btn" disabled={!!issue || pending !== null} onClick={finish}>
                Finish and show the AI's picks
              </button>
              <p className="muted" style={{ margin: 0, fontSize: "var(--fs-xs)" }}>
                Finish only when every good moment is marked. A good AI pick you didn't mark counts against it.
              </p>
            </div>
          ) : (
            <button className="btn ghost" style={{ marginTop: "var(--sp-3)" }} onClick={reopen}>Edit moments</button>
          )}
        </section>
      </div>

      {c && (
        <section className="glass" style={{ padding: "var(--sp-4)", display: "flex", flexDirection: "column", gap: "var(--sp-3)" }} aria-labelledby="h-compare">
          <h2 id="h-compare" style={{ fontSize: "var(--fs-lg)" }}>You compared with the AI</h2>
          <ScoreTiles score={c.stats} />
          {missed.length > 0 && (
            <div>
              <h3 style={{ fontSize: "var(--fs-md)", marginBottom: "var(--sp-1)" }}>
                Moments the AI missed ({missed.length} of {c.stats.moments}, {pct(missed.length, c.stats.moments)})
              </h3>
              <ul className="moment-list">
                {missed.map((m) => (
                  <li key={m.start}>
                    <button className="link-btn" onClick={() => seek(m.start)}>{fmtDuration(m.start)}–{fmtDuration(m.end)}</button>
                    {m.note && <span className="muted"> · {m.note}</span>}
                  </li>
                ))}
              </ul>
            </div>
          )}
          <div className="table-wrap">
            <table className="admin-table" style={{ minWidth: 0 }}>
              <thead><tr><th>AI pick</th><th>Title</th><th>AI score</th><th>Result</th></tr></thead>
              <tbody>
                {c.picks.map((p) => (
                  <tr key={p.start}>
                    <td><button className="link-btn" onClick={() => seek(p.start)}>{fmtDuration(p.start)}–{fmtDuration(p.end)}</button></td>
                    <td className="cell-clip" title={p.title}>{p.title || "—"}</td>
                    <td>{Math.round(p.score)}</td>
                    <td>{p.good ? "✓ On your moment" : "– Not marked"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </>
  );
}
