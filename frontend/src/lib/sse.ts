import { useEffect, useRef, useState } from "react";

/** Subscribe to a server-sent-events endpoint, parsing each `data:` line as JSON. */
export function useSSE<T>(url: string | null, active: boolean): T | null {
  const [data, setData] = useState<T | null>(null);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    if (!url || !active) return;
    const es = new EventSource(url, { withCredentials: true });
    esRef.current = es;
    es.onmessage = (e) => {
      try {
        setData(JSON.parse(e.data) as T);
      } catch {
        /* ignore */
      }
    };
    es.onerror = () => {
      es.close();
    };
    return () => {
      es.close();
      esRef.current = null;
    };
  }, [url, active]);

  return data;
}
