/** What a person should read. The server's words only if someone wrote them for a person: a framework
 * answers "Forbidden" or "LOGIN_BAD_CREDENTIALS", and a proxy sends a page of HTML. Those leave this empty,
 * so each caller's own wording shows. The raw answer stays in `data`. */
function readable(status: number, data: unknown): string {
  if (status === 0) return "We couldn't reach Kappacut. Check your connection and try again.";
  if (status === 401) return "You were signed out, so that didn't go through. Please try again.";
  const detail = data && typeof data === "object" && "detail" in data ? data.detail : null;
  if (typeof detail !== "string") return "";
  return /^[A-Z0-9_]+$/.test(detail) || /^(?:[A-Z][a-z]+ ?)+$/.test(detail) ? "" : detail;
}

export class ApiError extends Error {
  /** 0 means the request never got an answer */
  status: number;
  data: unknown;
  constructor(status: number, data: unknown) {
    super(readable(status, data));
    this.status = status;
    this.data = data;
  }
}

function csrfToken(): string | null {
  const m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : null;
}

const UNSAFE = new Set(["POST", "PUT", "PATCH", "DELETE"]);

export async function api<T = unknown>(path: string, opts: RequestInit = {}): Promise<T> {
  const method = (opts.method || "GET").toUpperCase();
  const headers = new Headers(opts.headers);
  if (UNSAFE.has(method)) {
    const t = csrfToken();
    if (t) headers.set("X-CSRF-Token", t);
  }
  if (typeof opts.body === "string" && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  let res: Response;
  try {
    res = await fetch(path, { ...opts, method, headers, credentials: "include" });
  } catch {
    throw new ApiError(0, null);               // the browser's own words here are "Failed to fetch"
  }
  if (res.status === 204) return undefined as T;

  const isJson = res.headers.get("content-type")?.includes("application/json");
  const data = isJson ? await res.json().catch(() => null) : await res.text();

  if (!res.ok) {
    if (res.status === 401) window.dispatchEvent(new Event("auth:expired"));
    throw new ApiError(res.status, data);
  }
  return data as T;
}

export const jget = <T,>(p: string) => api<T>(p);
export const jpost = <T,>(p: string, body?: unknown) =>
  api<T>(p, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
export const jdelete = <T,>(p: string) => api<T>(p, { method: "DELETE" });

/** OAuth2 password form (fastapi-users /login expects form-encoded). */
export async function loginForm(email: string, password: string): Promise<void> {
  const body = new URLSearchParams({ username: email, password });
  const res = await fetch("/api/auth/login", {
    method: "POST",
    body,
    credentials: "include",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
  });
  if (!res.ok) {
    const data = await res.json().catch(() => null);
    throw new ApiError(res.status, data);
  }
}
