export interface User {
  id: string;
  email: string;
  is_active: boolean;
  is_verified: boolean;
  is_superuser: boolean;
  display_name: string | null;
}

export type ProjectStatus = "uploading" | "queued" | "analyzing" | "ready" | "failed";

export interface ProjectSummary {
  id: string;
  name: string;
  status: ProjectStatus;
  duration: number;
  clips: number;
  rendered: number;
  has_edits: boolean;
  created_at: string;
  last_opened_at: string | null;
}

export interface JobState {
  status: "queued" | "running" | "done" | "error" | null;
  pct: number;
  msg: string;
  error: string | null;
}

export interface Clip {
  start_seconds: number;
  end_seconds: number;
  title: string;
  hook: string;
  why: string;
  quote: string;
  score: number;
  beat_type?: string;
  hook_line?: string;
  payoff_line?: string;
  duration?: number;
  auto?: Record<string, unknown>;
  [k: string]: unknown;
}

export interface Segment {
  start: number;
  end: number;
  text: string;
  words?: { w: string; start: number; end: number }[];
}

export interface ProjectDetail {
  id: string;
  name: string;
  status: ProjectStatus;
  duration: number;
  error: string | null;
  job: JobState | null;
  analysis: { segments: Segment[]; clips: Clip[] } | null;
}

export interface Usage {
  plan: string;
  minutes: { used: number; cap: number };
  storage_mb: { used: number; cap: number };
  renders_today: { used: number; cap: number };
  projects: { used: number; cap: number };
  max_upload_mb: number;
}

export interface SfxItem {
  id: string;
  file: string;
  name: string;
  builtin: boolean;
  url: string;
}

export interface RenderOutput {
  file: string;
  url: string;
  duration: number;
  vertical: boolean;
  captions?: boolean;
  sfx?: number;
}
