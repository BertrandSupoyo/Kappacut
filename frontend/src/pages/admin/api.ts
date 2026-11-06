import { useQuery } from "@tanstack/react-query";
import { jget } from "@/api/client";

/** failed: something went wrong, with a raw error behind it. stopped: it ended on purpose
 * (a Cancel, a refused video, a removed project). See app/admin_stats.py job_outcome. */
export type JobOutcome = "queued" | "running" | "done" | "failed" | "stopped";

export interface AdminJob {
  id: string;
  kind: string;
  status: "queued" | "running" | "done" | "error";
  outcome: JobOutcome;
  pct: number;
  msg: string;
  /** the sentence the user saw */
  error: string | null;
  /** what really happened */
  detail: string | null;
  user: string;
  project: string | null;
  project_id: string | null;
  worker: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface JobCounts {
  all: number;
  active: number;
  failed: number;
  stopped: number;
  done: number;
}

export interface AdminUser {
  id: string;
  email: string;
  is_verified: boolean;
  is_superuser: boolean;
  suspended: boolean;
  suspended_at: string | null;
  suspended_reason: string | null;
  unlimited: boolean;
  created_at: string;
  last_active: string | null;
  terms_version: string | null;
  terms_accepted_at: string | null;
  projects: number;
  minutes_used: number;
  minutes_cap: number;
  storage_mb: number;
  storage_cap_mb: number;
  minutes_override: number | null;
  storage_mb_override: number | null;
  takedowns: number;
}

export interface AttentionItem {
  level: "critical" | "warning" | "info";
  text: string;
  /** where to go to deal with it */
  to: string | null;
}

export interface OverviewDay {
  date: string;
  done: number;
  failed: number;
  minutes: number;
  signups: number;
}

export interface Overview {
  system: {
    db: boolean;
    redis: boolean;
    disk_free_pct: number;
    disk_ok: boolean;
    /** null: Redis couldn't say */
    workers: number | null;
    worker_concurrency: number;
  };
  attention: AttentionItem[];
  queue: { running: number; queued: number };
  jobs_7d: { done: number; failed: number };
  users: { total: number; new_7d: number; unverified: number; suspended: number; active_7d: number };
  usage: { minutes_month: number; minutes_last_month: number; clips_month: number; clips_last_month: number };
  storage_bytes: number;
  projects: number;
  days: OverviewDay[];
  live: AdminJob[];
  failures: AdminJob[];
}

/** Shared by the Overview page and the side navigation's counts. Stops while the tab is hidden. */
export function useOverview() {
  return useQuery({
    queryKey: ["admin", "overview"],
    queryFn: () => jget<Overview>("/api/admin/overview"),
    refetchInterval: 10_000,
  });
}
