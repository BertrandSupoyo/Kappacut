import { useEffect, useState, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import { ApiError } from "@/api/client";
import type { JobOutcome } from "./api";

export const PAGE_SIZE = 50;

export { fmtBytes } from "@/lib/format";
export { UsageMeter } from "@/components/UsageRows";

export function PageHead({ title, note, children }: { title: string; note?: ReactNode; children?: ReactNode }) {
  return (
    <header className="admin-head">
      <div style={{ minWidth: 0 }}>
        <h1>{title}</h1>
        {note && <p className="muted">{note}</p>}
      </div>
      {children}
    </header>
  );
}

interface LoadState {
  error: unknown;
  refetch: () => unknown;
}

function reason(error: unknown): string {
  if (error instanceof ApiError && error.status === 429) return "Too many requests just now. Wait a minute and try again.";
  if (error instanceof Error && error.message) return error.message;
  return "Something went wrong.";
}

/** Shown instead of a list that couldn't load, so a failed request never reads as "nothing here". */
export function LoadError({ query, what }: { query: LoadState; what: string }) {
  return (
    <div className="admin-error" role="alert">
      <span>
        <b>Couldn't load {what}.</b> <span className="muted">{reason(query.error)}</span>
      </span>
      <button className="btn ghost small" onClick={() => query.refetch()}>Try again</button>
    </div>
  );
}

/** For a page that refreshes itself: the last numbers stay up, marked as old. */
export function StaleNote({ show }: { show: boolean }) {
  if (!show) return null;
  return <p className="admin-stale" role="status">Couldn't refresh. These numbers may be out of date, and it will keep trying.</p>;
}

export function Loading() {
  return <div className="spin" role="status" aria-label="Loading" style={{ margin: "var(--sp-5) auto" }} />;
}

const OUTCOME: Record<JobOutcome, { label: string; color: string }> = {
  queued: { label: "Queued", color: "var(--amber)" },
  running: { label: "Running", color: "var(--signal)" },
  done: { label: "Done", color: "var(--good)" },
  failed: { label: "Failed", color: "var(--danger)" },
  stopped: { label: "Stopped", color: "var(--text-mut)" },
};

export function JobStatus({ outcome }: { outcome: JobOutcome }) {
  const { label, color } = OUTCOME[outcome];
  return (
    <span className="status-dot">
      <span aria-hidden style={{ background: color }} />
      {label}
    </span>
  );
}

/** Two clicks for something that can't be undone, without a browser dialog in the way. */
export function ConfirmButton({
  label,
  confirmLabel,
  disabled,
  onConfirm,
}: {
  label: string;
  confirmLabel: string;
  disabled?: boolean;
  onConfirm: () => void;
}) {
  const [armed, setArmed] = useState(false);
  if (!armed) {
    return <button className="btn danger small" disabled={disabled} onClick={() => setArmed(true)}>{label}</button>;
  }
  return (
    <span className="confirm-pair">
      <button className="btn danger small" autoFocus disabled={disabled} onClick={() => { setArmed(false); onConfirm(); }}>{confirmLabel}</button>
      <button className="btn ghost small" onClick={() => setArmed(false)}>Keep</button>
    </span>
  );
}

/** A list's filters live in the URL, so a link can open a filtered list and Back works. Changing
 * a filter goes back to the first page. */
export function useListParams<K extends string>(defaults: Record<K, string>) {
  const [params, setParams] = useSearchParams();
  const get = (key: K) => params.get(key) ?? defaults[key];
  const set = (changes: Partial<Record<K, string>>) =>
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      next.delete("page");
      for (const [key, value] of Object.entries(changes) as [K, string][]) {
        if (!value || value === defaults[key]) next.delete(key);
        else next.set(key, value);
      }
      return next;
    }, { replace: true });
  const page = Math.max(1, Number(params.get("page")) || 1);
  const setPage = (n: number) =>
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      if (n <= 1) next.delete("page");
      else next.set("page", String(n));
      return next;
    });
  return { get, set, page, setPage };
}

/** Waits for a pause in typing, so each keystroke isn't a request. */
export function SearchBox({ value, onChange, label, placeholder }: { value: string; onChange: (v: string) => void; label: string; placeholder: string }) {
  const [text, setText] = useState(value);
  useEffect(() => setText(value), [value]);
  useEffect(() => {
    if (text === value) return;
    const t = setTimeout(() => onChange(text.trim()), 300);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text]);
  return (
    <input
      className="admin-input admin-search"
      type="search"
      aria-label={label}
      placeholder={placeholder}
      value={text}
      onChange={(e) => setText(e.target.value)}
    />
  );
}

export function Pager({ total, page, onPage }: { total: number; page: number; onPage: (n: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  if (total <= PAGE_SIZE && page === 1) return null;
  const from = Math.min(total, (page - 1) * PAGE_SIZE + 1);
  const to = Math.min(total, page * PAGE_SIZE);
  return (
    <nav className="admin-pager" aria-label="Pages">
      <span className="muted">{from}–{to} of {total}</span>
      <button className="btn ghost small" disabled={page <= 1} onClick={() => onPage(page - 1)}>Previous</button>
      <button className="btn ghost small" disabled={page >= pages} onClick={() => onPage(page + 1)}>Next</button>
    </nav>
  );
}
