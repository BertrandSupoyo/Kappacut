export class ApiError extends Error {
  status: number;
  data: unknown;
  constructor(status: number, data: unknown) {
    super(
      (data && typeof data === "object" && "detail" in data && typeof data.detail === "string"
        ? data.detail
        : `HTTP ${status}`),
    );
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

  const res = await fetch(path, { ...opts, method, headers, credentials: "include" });
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
