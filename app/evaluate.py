"""How well do the AI's picks match what people actually do with them?

Compares each project's analysis (the clips the picker proposed) against the user's own
decisions: which clips they kept or skipped, how far they moved the edges, whether they
re-framed them, and which ones they rendered. `python -m app.cli eval-picks` prints the
report; `evaluate_project()` is the pure core (tests/test_evaluate.py).

What counts: only projects the user actually reviewed - a saved edit or a render after the
current analysis. An untouched project tells us nothing (nobody said yes or no), so it is
reported as coverage, not scored as "all kept".
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

TRIM_THRESHOLD = 1.0          # seconds an edge must move to count as "trimmed"
RENDER_OVERLAP = 0.5          # a rendered range covering half a pick counts as rendering it


def _score(c: dict) -> float:
    """The number the editor shows: 0-100 virality, or the older 1-10 score scaled."""
    if isinstance(c.get("virality"), (int, float)):
        return float(c["virality"])
    return float(c.get("score") or 0) * 10


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def _framing_changed(edit_look: dict | None, auto: dict | None) -> bool:
    """Did the user re-frame the clip away from the AI's suggestion?"""
    if not edit_look:
        return False
    crop = (auto or {}).get("crop") or {}
    if not crop:
        return False
    if edit_look.get("mode") != crop.get("mode"):
        return True
    if bool(edit_look.get("follow")) != bool(crop.get("follow")):
        return True
    return any(abs(float(edit_look.get(k, 0.5)) - float(crop.get(k, 0.5))) > 0.05 for k in ("px", "py", "zoom"))


def evaluate_project(clips: list[dict], edits: dict | None, rendered: list[tuple[float, float]]) -> dict:
    """Per-project numbers. `edits` is ProjectEdit.state["edits"] ({"<index>": ClipEdit});
    `rendered` the (start, end) ranges of renders made from this analysis."""
    edits = edits or {}
    rows = []
    for i, c in enumerate(clips):
        e = edits.get(str(i)) or edits.get(i) or {}
        a0, a1 = float(c["start_seconds"]), float(c["end_seconds"])
        s0, s1 = float(e.get("start", a0)), float(e.get("end", a1))
        length = max(a1 - a0, 0.1)
        rows.append({
            "kept": e.get("keep", True) is not False,
            "rendered": any(_overlap(a0, a1, r0, r1) >= RENDER_OVERLAP * length for r0, r1 in rendered),
            "start_shift": s0 - a0,
            "end_shift": s1 - a1,
            "trimmed": abs(s0 - a0) > TRIM_THRESHOLD or abs(s1 - a1) > TRIM_THRESHOLD,
            "reframed": _framing_changed(e.get("look"), c.get("auto")),
            "score": _score(c),
        })
    kept = [r for r in rows if r["kept"]]
    skipped = [r for r in rows if not r["kept"]]
    # pairwise accuracy: of every (kept, skipped) pair, how often did the kept one score higher?
    pairs = [(k["score"], s["score"]) for k in kept for s in skipped]
    return {
        "picks": len(rows),
        "kept": len(kept),
        "skipped": len(skipped),
        "rendered": sum(r["rendered"] for r in rows),
        "trimmed": sum(r["trimmed"] for r in rows),
        "reframed": sum(r["reframed"] for r in rows),
        "mean_start_shift": sum(r["start_shift"] for r in rows) / len(rows) if rows else 0.0,
        "mean_end_shift": sum(r["end_shift"] for r in rows) / len(rows) if rows else 0.0,
        "mean_abs_shift": sum(abs(r["start_shift"]) + abs(r["end_shift"]) for r in rows) / len(rows) if rows else 0.0,
        "score_kept": sum(r["score"] for r in kept) / len(kept) if kept else None,
        "score_skipped": sum(r["score"] for r in skipped) / len(skipped) if skipped else None,
        "pairs": len(pairs),
        "pairs_right": sum(1 for k, s in pairs if k > s) + 0.5 * sum(1 for k, s in pairs if k == s),
    }


@dataclass
class Report:
    analysed: int = 0
    reviewed: int = 0
    projects: list[tuple[str, dict]] = field(default_factory=list)

    def total(self) -> dict:
        keys = ("picks", "kept", "skipped", "rendered", "trimmed", "reframed", "pairs", "pairs_right")
        t = {k: sum(p[k] for _, p in self.projects) for k in keys}
        n = t["picks"] or 1
        t["mean_abs_shift"] = sum(p["mean_abs_shift"] * p["picks"] for _, p in self.projects) / n
        t["mean_start_shift"] = sum(p["mean_start_shift"] * p["picks"] for _, p in self.projects) / n
        t["mean_end_shift"] = sum(p["mean_end_shift"] * p["picks"] for _, p in self.projects) / n
        return t


async def build_report(session, user_email: str | None = None) -> Report:
    from sqlalchemy import select

    from app.models.job import Job
    from app.models.project import Analysis, Project, ProjectEdit
    from app.models.user import User

    q = select(Project, Analysis).join(Analysis, Analysis.project_id == Project.id)
    if user_email:
        q = q.join(User, User.id == Project.user_id).where(User.email == user_email)
    rep = Report()
    for project, analysis in (await session.execute(q.order_by(Project.created_at))).all():
        rep.analysed += 1
        edit = await session.get(ProjectEdit, project.id)
        since = analysis.created_at or dt.datetime.min.replace(tzinfo=dt.UTC)
        jobs = (await session.execute(
            select(Job).where(Job.project_id == project.id, Job.kind == "render",
                              Job.status == "done", Job.created_at >= since)
        )).scalars().all()
        rendered = [(float(r["start"]), float(r["end"]))
                    for j in jobs for r in ((j.payload or {}).get("ranges") or [])
                    if isinstance(r, dict) and "start" in r and "end" in r]
        if edit is None and not rendered:
            continue                                  # nobody reviewed it: no signal either way
        rep.reviewed += 1
        rep.projects.append((project.name, evaluate_project(
            analysis.clips or [], (edit.state or {}).get("edits") if edit else None, rendered)))
    return rep


def format_report(rep: Report) -> str:
    def pct(a, b):
        return f"{100 * a / b:.0f}%" if b else "—"

    lines = [f"Projects with an analysis: {rep.analysed} · reviewed by the user: {rep.reviewed}"]
    if not rep.projects:
        lines.append("Nothing to score yet: open projects, keep/skip/trim clips or render them, then re-run.")
        return "\n".join(lines)
    lines.append("")
    lines.append(f"{'project':<40} {'picks':>5} {'kept':>6} {'rendered':>9} {'trimmed':>8} {'reframed':>9} {'avg move':>9}")
    for name, p in rep.projects:
        lines.append(f"{name[:40]:<40} {p['picks']:>5} {pct(p['kept'], p['picks']):>6} "
                     f"{pct(p['rendered'], p['picks']):>9} {pct(p['trimmed'], p['picks']):>8} "
                     f"{pct(p['reframed'], p['picks']):>9} {p['mean_abs_shift']:>8.1f}s")
    t = rep.total()
    lines.append("-" * 92)
    lines.append(f"{'ALL':<40} {t['picks']:>5} {pct(t['kept'], t['picks']):>6} {pct(t['rendered'], t['picks']):>9} "
                 f"{pct(t['trimmed'], t['picks']):>8} {pct(t['reframed'], t['picks']):>9} {t['mean_abs_shift']:>8.1f}s")
    lines.append("")
    lines.append(f"Edges: starts moved {t['mean_start_shift']:+.1f}s, ends moved {t['mean_end_shift']:+.1f}s on average "
                 "(negative = you wanted it earlier).")
    if t["pairs"]:
        lines.append(f"Score ranking: a kept clip outscored a skipped one in {pct(t['pairs_right'], t['pairs'])} "
                     f"of {t['pairs']} pairs (50% = no better than chance).")
    else:
        lines.append("Score ranking: no skipped clips yet, so the score can't be checked against your choices.")
    return "\n".join(lines)
