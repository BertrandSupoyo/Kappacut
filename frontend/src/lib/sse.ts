import { useEffect, useRef, useState } from "react";

/** Subscribe to a server-sent-events endpoint, parsing each `data:` line as JSON. */
export function useSSE<T>(url: string | null, active: boolean): T | null {
  const [data, setData] = useState<T | null>(null);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    if (!url || !active) return;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let finished = false;
    // reconnects in a row without a single message: a stream that keeps failing before it
    // says anything (session expired, project deleted) backs off and then stops
    let failures = 0;
    const open = () => {
      const es = new EventSource(url, { withCredentials: true });
      esRef.current = es;
      es.onmessage = (e) => {
        failures = 0;
        try {
          const msg = JSON.parse(e.data) as T & { status?: string | null };
          finished = msg?.status === "done" || msg?.status === "error";
          setData(msg);
        } catch {
          /* ignore */
        }
      };
      // The server ends the stream when the job finishes — and also when it has no job yet
      // (status still "uploading"), on a proxy timeout, or on a worker restart. Closing for
      // good on any of those froze the bar at 0% while the job ran, so reopen unless the
      // job actually ended. (The editor's 2.5 s project poll still moves the bar if we stop.)
      es.onerror = () => {
        es.close();
        failures += 1;
        if (!finished && failures <= 8) retry = setTimeout(open, Math.min(2000 * 2 ** (failures - 1), 30000));
      };
    };
    open();
    return () => {
      clearTimeout(retry);
      esRef.current?.close();
      esRef.current = null;
      // forget this run's last message when it ends: otherwise the next activation's
      // first render still sees e.g. the previous render's "done" and stops itself
      setData(null);
    };
  }, [url, active]);

  return data;
}
