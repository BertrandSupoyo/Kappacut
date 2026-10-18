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
  /** the "How many clips" choice, when one was made (a maximum, not a quota) */
  requested?: number | null;
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
  /** 0-100, built from `breakdown` (see clipfinder.score_breakdown) */
  virality?: number;
  breakdown?: ScoreBreakdown;
  /** median face centre (0-1 of the frame) when one shows in most sampled frames */
  face?: ClipFace;
  [k: string]: unknown;
}

export interface ScoreBreakdown {
  hook: number;
  story: number;
  energy: number;
  clarity: number;
  pacing: number;
}

export interface ClipFace {
  x: number;
  y: number;
  size: number;
  seen: number;
  ar: number;
  /** smoothed camera path [source_seconds, x, y] the render follows when "Follow face" is on */
  track?: [number, number, number][];
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
