import { useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Uppy from "@uppy/core";
import Tus from "@uppy/tus";
import { ApiError, jdelete, jget, jpost } from "@/api/client";
import type { ProjectSummary } from "@/api/types";
import { useToast } from "@/components/Toast";
import { fmtAgo, fmtDuration } from "@/lib/format";
import { Row, Seg, Toggle } from "@/editor/controls";

const VIDEO_ACCEPT = ".mp4,.mkv,.webm,.mov,.avi,.m4v,.m2ts,.mts";

type Platform = "vertical" | "shorts" | "general";
const PLATFORMS: { id: Platform; label: string }[] = [
  { id: "vertical", label: "Vertical (TikTok/Reels)" },
  { id: "shorts", label: "YouTube Shorts" },
  { id: "general", label: "General" },
];

type Vibe = "reaction" | "fail" | "bit" | "story" | "quote" | "wholesome" | "";
const VIBES: { id: Vibe; label: string }[] = [
  { id: "reaction", label: "Reaction" },
  { id: "fail", label: "Fail" },
  { id: "bit", label: "Bit" },
  { id: "story", label: "Story" },
  { id: "quote", label: "Quote" },
  { id: "wholesome", label: "Wholesome" },
];

type Length = "short" | "medium" | "long" | "";
const LENGTHS: { id: Length; label: string }[] = [
  { id: "short", label: "<15s" },
  { id: "medium", label: "15-30s" },
  { id: "long", label: "30-60s" },
];
const LENGTH_TEXT: Record<Exclude<Length, "">, string> = { short: "under 15s", medium: "15-30s", long: "30-60s" };

type Count = "3" | "5" | "10" | "";
const COUNTS: { id: Count; label: string }[] = [
  { id: "3", label: "3" },
  { id: "5", label: "5" },
  { id: "10", label: "10" },
];

interface Guide {
  platform: Platform | "";
  vibe: Vibe;
  length: Length;
  count: Count;
  avoid: string;
}

const DEFAULT_GUIDE: Guide = { platform: "", vibe: "", length: "", count: "", avoid: "" };

function composeTaste(g: Guide): string {
  const parts: string[] = [];
  if (g.vibe) parts.push(`Prioritize beat_type: ${g.vibe}.`);
  if (g.length) parts.push(`Target length ${LENGTH_TEXT[g.length]}.`);
  if (g.count) parts.push(`${g.count} clips.`);
  if (g.platform) parts.push(`Format: ${PLATFORMS.find((p) => p.id === g.platform)?.label}.`);
  if (g.avoid.trim()) parts.push(`Avoid: ${g.avoid.trim()}.`);
  return parts.join(" ");
}

const STATUS_LABEL: Record<string, string> = {
  uploading: "Uploading",
  queued: "Queued",
  analyzing: "Analyzing",
  ready: "Ready",
  failed: "Failed",
};

export function Projects() {
  const qc = useQueryClient();
  const toast = useToast();
  const nav = useNavigate();
  const fileInput = useRef<HTMLInputElement>(null);
  const [uploadPct, setUploadPct] = useState<number | null>(null);
  const [showGuide, setShowGuide] = useState(false);
  const [guide, setGuide] = useState<Guide>(DEFAULT_GUIDE);
  const [autoRender, setAutoRender] = useState(false);

  const { data, isLoading } = useQuery({
    queryKey: ["projects"],
    queryFn: () => jget<{ projects: ProjectSummary[] }>("/api/projects"),
    refetchInterval: (q) => {
      const busy = q.state.data?.projects.some((p) => ["uploading", "queued", "analyzing"].includes(p.status));
      return busy ? 3000 : false;
    },
  });

  const del = useMutation({
    mutationFn: (id: string) => jdelete(`/api/projects/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["projects"] }),
  });

  async function startUpload(file: File, taste: string, autoRenderFlag: boolean) {
    setUploadPct(0);
    let projectId: string;
    try {
      const res = await jpost<{ id: string }>("/api/projects", { filename: file.name, taste, auto_render: autoRenderFlag });
      projectId = res.id;
    } catch (e) {
      setUploadPct(null);
      toast(e instanceof ApiError && e.status === 429 ? "You've hit your project limit." : "Couldn't create the project.", "error");
      return;
    }

    const uppy = new Uppy({ autoProceed: true }).use(Tus, {
      endpoint: "/files/",
      chunkSize: 8 * 1024 * 1024,
      withCredentials: true,
      removeFingerprintOnSuccess: true,
    });
    uppy.addFile({ name: file.name, type: file.type, data: file, meta: { projectId, filename: file.name } });
    uppy.on("upload-progress", (_f, prog) => {
      if (prog.bytesTotal) setUploadPct(Math.round((prog.bytesUploaded / prog.bytesTotal) * 100));
    });
    uppy.on("complete", (result) => {
      setUploadPct(null);
      uppy.destroy();
      if (result.failed && result.failed.length) {
        const msg = result.failed[0]?.error || "";
        toast(/quota|limit/i.test(String(msg)) ? "Upload rejected — over your limit." : "Upload failed.", "error");
        void del.mutateAsync(projectId).catch(() => {});
      } else {
        qc.invalidateQueries({ queryKey: ["projects"] });
        nav(`/app/p/${projectId}`);
      }
    });
  }

  const projects = data?.projects ?? [];

  return (
    <div>
      <div style={{ display: "flex", alignItems: "baseline", gap: "var(--sp-4)", margin: "var(--sp-4) 0 var(--sp-5)" }}>
        <h1 style={{ fontSize: "var(--fs-2xl)" }}>Projects</h1>
        <span className="muted" style={{ fontSize: "var(--fs-sm)" }}>{projects.length}</span>
        <div style={{ marginLeft: "auto" }}>
          <input
            ref={fileInput}
            type="file"
            accept={VIDEO_ACCEPT}
            hidden
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) void startUpload(f, composeTaste(guide), autoRender);
              e.target.value = "";
              setShowGuide(false);
              setGuide(DEFAULT_GUIDE);
              setAutoRender(false);
            }}
          />
          <button
            className="btn"
            disabled={uploadPct !== null}
            onClick={() => setShowGuide((v) => !v)}
          >
            {uploadPct !== null ? `Uploading ${uploadPct}%` : showGuide ? "Cancel" : "New project"}
          </button>
        </div>
      </div>

      {showGuide && uploadPct === null && (
        <div className="glass" style={{ padding: "var(--sp-4)", marginBottom: "var(--sp-4)", display: "flex", flexDirection: "column", gap: "var(--sp-1)" }}>
          <Row label="Platform">
            <Seg options={PLATFORMS} value={guide.platform} onChange={(v) => setGuide((g) => ({ ...g, platform: v }))} />
          </Row>
          <Row label="Vibe">
            <Seg options={VIBES} value={guide.vibe} onChange={(v) => setGuide((g) => ({ ...g, vibe: v }))} />
          </Row>
          <Row label="Clip length">
            <Seg options={LENGTHS} value={guide.length} onChange={(v) => setGuide((g) => ({ ...g, length: v }))} />
          </Row>
          <Row label="How many clips">
            <Seg options={COUNTS} value={guide.count} onChange={(v) => setGuide((g) => ({ ...g, count: v }))} />
          </Row>
          <Row label="Anything to avoid (optional)">
            <div className="field">
              <input
                placeholder="profanity, spoilers, small talk…"
                value={guide.avoid}
                onChange={(e) => setGuide((g) => ({ ...g, avoid: e.target.value }))}
              />
            </div>
          </Row>
          <Row label="Review before rendering?">
            <Toggle
              on={!autoRender}
              onChange={(v) => setAutoRender(!v)}
              label={autoRender ? "Off — render automatically once picks are ready" : "On — I'll review clips in the editor first"}
            />
          </Row>
          <div>
            <button className="btn" style={{ marginTop: "var(--sp-2)" }} onClick={() => fileInput.current?.click()}>
              Choose video…
            </button>
          </div>
        </div>
      )}

      {uploadPct !== null && (
        <div className="glass" style={{ padding: "var(--sp-3) var(--sp-4)", marginBottom: "var(--sp-4)" }}>
          <div style={{ height: 4, background: "var(--sub-bg)", borderRadius: 99, overflow: "hidden" }}>
            <div style={{ width: `${uploadPct}%`, height: "100%", background: "var(--accent)", transition: "width .2s" }} />
          </div>
        </div>
      )}

      {isLoading ? (
        <div className="glass" style={{ padding: "var(--sp-6)", textAlign: "center" }}>
          <div className="spin" style={{ margin: "0 auto" }} />
        </div>
      ) : projects.length === 0 ? (
        <div className="glass" style={{ padding: "var(--sp-6)", textAlign: "center" }}>
          <p className="muted" style={{ margin: 0 }}>No projects yet. Upload a video to get your first shortlist.</p>
        </div>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: "var(--sp-4)" }}>
          {projects.map((p) => (
            <div key={p.id} className="glass" style={{ padding: "var(--sp-4)", display: "flex", flexDirection: "column", gap: "var(--sp-2)" }}>
              <div style={{ display: "flex", alignItems: "center", gap: "var(--sp-2)" }}>
                <StatusDot status={p.status} />
                <span className="muted" style={{ fontSize: "var(--fs-2xs)", textTransform: "uppercase", letterSpacing: ".08em" }}>
                  {STATUS_LABEL[p.status]}
                </span>
                <button
                  className="btn danger"
                  style={{ marginLeft: "auto", padding: "2px 8px", fontSize: "var(--fs-2xs)" }}
                  onClick={() => confirm(`Delete "${p.name}"?`) && del.mutate(p.id)}
                >
                  Delete
                </button>
              </div>
              <Link to={`/app/p/${p.id}`} style={{ color: "var(--text)", fontFamily: "'Space Grotesk'", fontWeight: 600 }}>
                {p.name}
              </Link>
              <div className="muted" style={{ fontSize: "var(--fs-xs)" }}>
                {p.duration ? fmtDuration(p.duration) : "—"} · {p.clips} clip{p.clips === 1 ? "" : "s"}
                {p.rendered > 0 && ` · ${p.rendered} rendered`}
                {p.has_edits && " · edited"}
              </div>
              <div className="muted" style={{ fontSize: "var(--fs-2xs)" }}>{fmtAgo(p.last_opened_at || p.created_at)}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function StatusDot({ status }: { status: string }) {
  const color =
    status === "ready" ? "var(--good)" : status === "failed" ? "var(--danger)" : status === "analyzing" ? "var(--cyan)" : "var(--amber)";
  const pulse = ["uploading", "queued", "analyzing"].includes(status);
  return (
    <span
      style={{
        width: 7,
        height: 7,
        borderRadius: "50%",
        background: color,
        boxShadow: pulse ? `0 0 0 0 ${color}` : "none",
        animation: pulse ? "dot 1.4s infinite" : "none",
      }}
    />
  );
}
