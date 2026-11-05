import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { jget, jpost } from "@/api/client";
import { useToast } from "@/components/Toast";
import { fmtAgo, fmtDuration } from "@/lib/format";
import { AdminOnly } from "./Admin";

export interface Score {
  moments: number;
  found: number;
  picks: number;
  good: number;
  pairs: number;
  pairs_right: number;
}

type Status = "none" | "draft" | "done" | "stale";

interface VideoRow {
  id: string;
  name: string;
  seconds: number | null;
  category: string | null;
  status: Status;
  moments: number;
  blind: boolean;
  updated_at: string | null;
  score: Score | null;
}

interface Summary {
  score: Score & { videos: number };
  blind_score: Score & { videos: number };
  stale: string[];
  training: { videos: number; hand_labelled: number; pairs: number; needed: number };
  usage: {
    analysed: number;
    reviewed: number;
    picks: number;
    kept: number;
    rendered: number;
    trimmed: number;
    pairs: number;
    pairs_right: number;
  };
  weights: Record<string, number>;
}

interface Run {
  id: string;
  created_at: string;
  score: Score & { videos: number };
  result: {
    videos: number;
    hand_labelled: number;
    pairs: number;
    weights: Record<string, number>;
    hand_accuracy: number | null;
    learned_accuracy: number | null;
    better: number;
    worse: number;
    same: number;
    p: number | null;
    wins: boolean;
    verdict: string;
    paste: string | null;
  };
}

const STATUS_TEXT: Record<Status, string> = {
  none: "Not labelled",
  draft: "In progress",
  done: "Finished",
  stale: "Video replaced",
};

export const pct = (a: number, b: number) => (b ? `${Math.round((100 * a) / b)}%` : "—");
const share = (v: number | null) => (v === null ? "—" : `${Math.round(v * 100)}%`);

export function StatTile({ label, value, sub }: { label: string; value: string; sub: string }) {
  return (
    <div className="stat-tile">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      <div className="stat-sub">{sub}</div>
    </div>
  );
}

export function ScoreTiles({ score }: { score: Score }) {
  return (
    <div className="stat-row">
      <StatTile label="Found" value={pct(score.found, score.moments)} sub={`${score.found} of ${score.moments} moments you marked`} />
      <StatTile label="Good" value={pct(score.good, score.picks)} sub={`${score.good} of ${score.picks} AI picks`} />
      <StatTile
        label="Ranking"
        value={pct(score.pairs_right, score.pairs)}
        sub={score.pairs ? `of ${score.pairs} good-versus-bad pairs · 50% is chance` : "Needs a video with good and bad picks"}
      />
    </div>
  );
}

export function AdminTraining() {
  return (
    <AdminOnly>
      <div className="training" style={{ display: "flex", flexDirection: "column", gap: "var(--sp-5)", margin: "var(--sp-4) 0" }}>
        <div>
          <Link to="/admin" className="muted" style={{ fontSize: "var(--fs-sm)" }}>← Admin</Link>
          <h1 style={{ fontSize: "var(--fs-2xl)", marginTop: "var(--sp-1)" }}>Training</h1>
          <p className="muted" style={{ margin: "var(--sp-1) 0 0", fontSize: "var(--fs-sm)" }}>
            Mark the good moments in a video, see how the AI compares, then train the score on what you marked.
          </p>
        </div>
        <ScoreSection />
        <VideosSection />
        <TrainSection />
        <UsageSection />
      </div>
    </AdminOnly>
  );
}

function useSummary() {
  return useQuery({
    queryKey: ["admin", "training", "summary"],
    queryFn: () => jget<Summary>("/api/admin/training/summary"),
  });
}

function ScoreSection() {
  const { data, error } = useSummary();
  return (
    <section className="glass" style={{ padding: "var(--sp-4)" }} aria-labelledby="h-score">
      <h2 id="h-score" style={{ fontSize: "var(--fs-lg)", marginBottom: "var(--sp-3)" }}>AI score</h2>
      {error ? (
        <p className="err" style={{ margin: 0 }}>{(error as Error).message}</p>
      ) : !data ? (
        <p className="muted" style={{ margin: 0 }}>Loading…</p>
      ) : data.score.videos === 0 ? (
        <p className="muted" style={{ margin: 0 }}>No score yet. Finish labelling a video below to get one.</p>
      ) : (
        <>
          <ScoreTiles score={data.score} />
          <p className="muted" style={{ margin: "var(--sp-3) 0 0", fontSize: "var(--fs-xs)" }}>
            From {data.score.videos} labelled {data.score.videos === 1 ? "video" : "videos"}.{" "}
            {data.blind_score.videos === data.score.videos
              ? "All were marked before seeing the AI's picks."
              : data.blind_score.videos === 0
                ? "None were marked before seeing the AI's picks, so this may flatter the AI."
                : `Only the ${data.blind_score.videos} marked before seeing the AI's picks: Found ${pct(data.blind_score.found, data.blind_score.moments)}, Good ${pct(data.blind_score.good, data.blind_score.picks)}.`}
          </p>
        </>
      )}
      {data && data.stale.length > 0 && (
        <p className="err" style={{ margin: "var(--sp-3) 0 0" }}>
          Not scored because the video was replaced after labelling: {data.stale.join(", ")}.
        </p>
      )}
    </section>
  );
}

function VideosSection() {
  const { data, error } = useQuery({
    queryKey: ["admin", "training", "videos"],
    queryFn: () => jget<{ videos: VideoRow[] }>("/api/admin/training/videos"),
  });
  const videos = data?.videos ?? [];
  const finished = videos.filter((v) => v.status === "done").length;
  return (
    <section className="glass" style={{ padding: "var(--sp-4)" }} aria-labelledby="h-videos">
      <div style={{ display: "flex", alignItems: "baseline", gap: "var(--sp-3)", flexWrap: "wrap", marginBottom: "var(--sp-3)" }}>
        <h2 id="h-videos" style={{ fontSize: "var(--fs-lg)" }}>Your videos</h2>
        <span className="muted" style={{ fontSize: "var(--fs-xs)" }}>
          {finished} of {videos.length} labelled · label a video before opening it in the editor
        </span>
      </div>
      {error ? (
        <p className="err" style={{ margin: 0 }}>{(error as Error).message}</p>
      ) : videos.length === 0 ? (
        <p className="muted" style={{ margin: 0 }}>
          Nothing to label yet. Upload a video in <Link to="/app">Projects</Link> and wait for its analysis to finish.
        </p>
      ) : (
        <div className="table-wrap">
          <table className="admin-table">
            <thead>
              <tr><th>Video</th><th>Length</th><th>Status</th><th>Moments</th><th>Found</th><th>Good</th><th /></tr>
            </thead>
            <tbody>
              {videos.map((v) => (
                <tr key={v.id}>
                  <td className="cell-clip" title={v.name}>{v.name}</td>
                  <td>{v.seconds ? fmtDuration(v.seconds) : "—"}</td>
                  <td>
                    {STATUS_TEXT[v.status]}
                    {v.status === "done" && <span className="muted"> · {v.blind ? "blind" : "not blind"}</span>}
                  </td>
                  <td>{v.status === "none" ? "—" : v.moments}</td>
                  <td>{v.score ? pct(v.score.found, v.score.moments) : "—"}</td>
                  <td>{v.score ? pct(v.score.good, v.score.picks) : "—"}</td>
                  <td>
                    <Link className="btn ghost" style={{ padding: "4px 12px" }} to={`/admin/training/${v.id}`}>
                      {v.status === "done" ? "Review" : v.status === "none" ? "Label" : "Continue"}
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function TrainSection() {
  const qc = useQueryClient();
  const toast = useToast();
  const { data: summary } = useSummary();
  const { data } = useQuery({
    queryKey: ["admin", "training", "runs"],
    queryFn: () => jget<{ runs: Run[] }>("/api/admin/training/runs"),
  });
  const run = useMutation({
    mutationFn: () => jpost<Run>("/api/admin/training/runs"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "training", "runs"] }),
    onError: (e) => toast((e as Error).message || "Training failed.", "error"),
  });
  const runs = data?.runs ?? [];
  const latest = runs[0];
  const t = summary?.training;
  const filled = t ? Math.min(100, (t.videos / t.needed) * 100) : 0;

  return (
    <section className="glass" style={{ padding: "var(--sp-4)" }} aria-labelledby="h-train">
      <div style={{ display: "flex", alignItems: "center", gap: "var(--sp-3)", flexWrap: "wrap", marginBottom: "var(--sp-3)" }}>
        <h2 id="h-train" style={{ fontSize: "var(--fs-lg)" }}>Train the score</h2>
        <button
          className="btn"
          style={{ marginLeft: "auto", padding: "6px 14px" }}
          disabled={!t || t.videos === 0 || run.isPending}
          onClick={() => run.mutate()}
        >
          {run.isPending ? "Training…" : "Run training"}
        </button>
      </div>
      <p className="muted" style={{ margin: "0 0 var(--sp-3)", fontSize: "var(--fs-sm)" }}>
        Learns how much hook, story, energy and the other parts should count, then tests that on videos it never trained on.
        Live scoring doesn't change until you edit one line in <code>kappacut.py</code>. It reorders the AI's picks but can't recover a moment the AI missed.
      </p>
      {t && (
        <div style={{ marginBottom: "var(--sp-3)" }}>
          <div
            className="meter"
            role="meter"
            aria-label="Videos ready for training"
            aria-valuemin={0}
            aria-valuemax={t.needed}
            aria-valuenow={Math.min(t.videos, t.needed)}
          >
            <span style={{ width: `${filled}%` }} />
          </div>
          <p className="muted" style={{ margin: "var(--sp-1) 0 0", fontSize: "var(--fs-xs)" }}>
            {t.videos} of the {t.needed} videos needed ({t.hand_labelled} hand-labelled, {t.videos - t.hand_labelled} from Keep/Skip), {t.pairs} good-versus-bad pairs.
            A video only counts if it has both a good and a bad pick. Aim for 20–40.
          </p>
        </div>
      )}
      {latest ? (
        <>
          <p style={{ margin: "0 0 var(--sp-2)", fontSize: "var(--fs-sm)" }}><b>{latest.result.verdict}</b></p>
          {latest.result.paste && (
            <pre className="paste-line"><code>{latest.result.paste}</code></pre>
          )}
          {latest.result.hand_accuracy !== null && (
            <p className="muted" style={{ margin: "0 0 var(--sp-3)", fontSize: "var(--fs-xs)" }}>
              On videos it never trained on: current weights {share(latest.result.hand_accuracy)}, learned {share(latest.result.learned_accuracy)}.
              Better on {latest.result.better}, worse on {latest.result.worse}, same on {latest.result.same}. Chance this is luck: {share(latest.result.p)}.
            </p>
          )}
          {summary && Object.keys(latest.result.weights).length > 0 && (
            <div className="table-wrap" style={{ marginBottom: "var(--sp-4)" }}>
              <table className="admin-table" style={{ minWidth: 0, maxWidth: 360 }}>
                <thead><tr><th>Part</th><th>Current</th><th>Learned</th></tr></thead>
                <tbody>
                  {Object.entries(summary.weights).map(([part, current]) => (
                    <tr key={part}><td>{part}</td><td>{current}</td><td>{latest.result.weights[part] ?? "—"}</td></tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <h3 style={{ fontSize: "var(--fs-md)", marginBottom: "var(--sp-2)" }}>Past runs</h3>
          <div className="table-wrap">
            <table className="admin-table">
              <thead>
                <tr><th>When</th><th>Labelled</th><th>Found</th><th>Good</th><th>Ranking</th><th>Current</th><th>Learned</th><th>Result</th></tr>
              </thead>
              <tbody>
                {runs.map((r) => (
                  <tr key={r.id}>
                    <td className="muted">{fmtAgo(r.created_at)}</td>
                    <td>{r.score.videos}</td>
                    <td>{pct(r.score.found, r.score.moments)}</td>
                    <td>{pct(r.score.good, r.score.picks)}</td>
                    <td>{pct(r.score.pairs_right, r.score.pairs)}</td>
                    <td>{share(r.result.hand_accuracy)}</td>
                    <td>{share(r.result.learned_accuracy)}</td>
                    <td>{r.result.wins ? "Learned weights won" : "Kept current"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : (
        <p className="muted" style={{ margin: 0, fontSize: "var(--fs-sm)" }}>
          {t && t.videos === 0 ? "Nothing to train on yet: no video has both a good and a bad pick." : "No runs yet."}
        </p>
      )}
    </section>
  );
}

function UsageSection() {
  const { data } = useSummary();
  const u = data?.usage;
  if (!u) return null;
  return (
    <section className="glass" style={{ padding: "var(--sp-4)" }} aria-labelledby="h-usage">
      <div style={{ display: "flex", alignItems: "baseline", gap: "var(--sp-3)", flexWrap: "wrap", marginBottom: "var(--sp-3)" }}>
        <h2 id="h-usage" style={{ fontSize: "var(--fs-lg)" }}>From real use</h2>
        <span className="muted" style={{ fontSize: "var(--fs-xs)" }}>
          What all users did with the AI's picks · {u.reviewed} of {u.analysed} projects opened and changed
        </span>
      </div>
      <div className="stat-row">
        <StatTile label="Kept" value={pct(u.kept, u.picks)} sub={`${u.kept} of ${u.picks} picks · untouched counts as kept`} />
        <StatTile label="Rendered" value={pct(u.rendered, u.picks)} sub={`${u.rendered} of ${u.picks} picks`} />
        <StatTile label="Trimmed" value={pct(u.trimmed, u.picks)} sub={`${u.trimmed} of ${u.picks} picks`} />
        <StatTile
          label="Score ranking"
          value={pct(u.pairs_right, u.pairs)}
          sub={u.pairs ? `kept beat skipped in ${u.pairs} pairs · 50% is chance` : "Needs a skipped clip"}
        />
      </div>
    </section>
  );
}
