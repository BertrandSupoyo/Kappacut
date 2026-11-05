"""Learn the score weights from what people said about the picks, and test them on videos they
never saw.

What gets trained: the six numbers in kappacut.SCORE_WEIGHTS that turn a clip's parts (hook,
story, energy, ...) into the 0-100 score the shortlist is ranked by. Not the language models:
those run on Groq, and retraining one takes GPUs and far more examples than this will have.
This takes a few seconds on a CPU and no AI calls, because every clip already stores its parts.

Admin > Training and `python -m app.cli train-weights` report and change nothing. If the new
weights win, they give the line to paste over SCORE_WEIGHTS, so adopting them is a one-line edit
someone reviewed (the rule in README "The clip-picking model": keep a change only if it wins).

Examples are (good, bad) pairs of picks from one video:
- a video with a finished label: picks on a marked moment are good, the others bad
- otherwise the editor's Keep/Skip. A video with no skip gives no pairs, so the many projects
  nobody reviewed (every clip "kept" by default) can't teach it that everything is good.
  Re-analysing a project deletes its Keep/Skip (saved edits go by clip index); labels survive.

It only sees clips that made the shortlist and only reorders picks, so it can't fix a moment
the picker missed: `eval-labels` measures that, and prompts or models are the fix. "Good" means
what the labeller liked, not what got views.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np

import kappacut as cf
from app.evaluate import matches_moment

PARTS = tuple(cf.SCORE_WEIGHTS)
HAND = np.array([cf.SCORE_WEIGHTS[k] for k in PARTS], dtype=float) / sum(cf.SCORE_WEIGHTS.values())

MIN_VIDEOS = 10         # below this one odd video can swing the held-out result
MIN_LIFT = 0.02         # held-out accuracy gain worth a change to what every user sees
MAX_P = 0.10            # how likely that gain may be from luck (paired sign-flip test)
SHARPNESS = 20.0        # a 10-point score gap reads as 88% sure the higher clip is better
PULL = 1.5              # pull towards the hand-set weights, divided by the number of videos:
                        # one video barely moves them, dozens can
STEPS = 3000
RATE = 0.25


def features(clip: dict, energy_weight: float = 1.0) -> np.ndarray | None:
    """The clip's parts as score_breakdown sums them (category energy weight, judge gate), so
    weights @ features orders clips exactly as the product would with those weights. None for
    an older analysis whose breakdown lacks a part."""
    b = clip.get("breakdown") or {}
    if any(k not in b for k in PARTS):
        return None
    v = np.array([float(b[k]) for k in PARTS])
    v[PARTS.index("energy")] *= max(0.0, energy_weight)
    if clip.get("arc"):
        quality = (b["hook"] + b["story"]) / 2
        gate = cf.GATE_MIN + (1 - cf.GATE_MIN) * min(1.0, quality / cf.GATE_FULL)
        for i, k in enumerate(PARTS):
            if k not in cf.JUDGED_PARTS:
                v[i] *= gate
    return v


def project_pairs(clips: list[dict], energy_weight: float = 1.0,
                  moments: list[tuple[float, float]] | None = None,
                  edits: dict | None = None) -> np.ndarray:
    """(good - bad) feature differences, one row per pair. Hand labels win over Keep/Skip."""
    edits = edits or {}
    good, bad = [], []
    for i, c in enumerate(clips):
        v = features(c, energy_weight)
        if v is None:
            continue
        if moments is not None:
            span = (float(c["start_seconds"]), float(c["end_seconds"]))
            is_good = any(matches_moment(*span, *m) for m in moments)
        else:
            is_good = (edits.get(str(i)) or edits.get(i) or {}).get("keep", True) is not False
        (good if is_good else bad).append(v)
    if not good or not bad:
        return np.empty((0, len(PARTS)))
    return np.array([g - b for g in good for b in bad])


def fit(pair_sets: list[np.ndarray]) -> np.ndarray:
    """Pairwise logistic regression: raise the chance each good clip outscores its bad one.
    Every video counts the same however many pairs it has. Weights stay positive and sum to 1
    (softmax), since a better hook should never lower a score."""
    x = np.vstack(pair_sets)
    share = np.concatenate([np.full(len(p), 1 / (len(p) * len(pair_sets))) for p in pair_sets])
    start = np.log(np.maximum(HAND, 1e-6))
    theta = start.copy()
    for _ in range(STEPS):
        w = np.exp(theta) / np.exp(theta).sum()
        g = -SHARPNESS * x.T @ (share / (1 + np.exp(SHARPNESS * x @ w)))
        theta -= RATE * (w * (g - w @ g) + 2 * PULL / len(pair_sets) * (theta - start))
    return np.exp(theta) / np.exp(theta).sum()


def pair_accuracy(weights: np.ndarray, pairs: np.ndarray) -> float:
    """Share of pairs where the good clip scored higher; a tie is half."""
    m = pairs @ weights
    return float(((m > 1e-9).sum() + 0.5 * (np.abs(m) <= 1e-9).sum()) / len(pairs))


def luck_chance(gains: np.ndarray) -> float:
    """If the new weights were no better, how often would random win/lose flips of these
    per-video gains average at least this much?"""
    flips = np.random.default_rng(0).choice([-1.0, 1.0], size=(20000, len(gains)))
    return float((((flips * gains).mean(axis=1) >= gains.mean()).sum() + 1) / 20001)


@dataclass
class Video:
    name: str
    pairs: np.ndarray
    hand_labelled: bool = False


@dataclass
class Result:
    videos: int = 0
    hand_labelled: int = 0
    pairs: int = 0
    weights: dict[str, int] = field(default_factory=dict)
    hand_accuracy: float | None = None     # held-out, each video weighs the same
    learned_accuracy: float | None = None
    better: int = 0
    worse: int = 0
    same: int = 0
    p: float | None = None
    wins: bool = False


def train(videos: list[Video]) -> Result:
    videos = [v for v in videos if len(v.pairs)]
    r = Result(videos=len(videos), hand_labelled=sum(v.hand_labelled for v in videos),
               pairs=sum(len(v.pairs) for v in videos))
    if not videos:
        return r
    final = fit([v.pairs for v in videos])
    r.weights = {k: round(float(w) * 100) for k, w in zip(PARTS, final)}
    if len(videos) < 2:
        return r
    hand = np.array([pair_accuracy(HAND, v.pairs) for v in videos])
    held = np.array([pair_accuracy(fit([o.pairs for o in videos if o is not v]), v.pairs) for v in videos])
    gains = held - hand
    r.hand_accuracy, r.learned_accuracy = float(hand.mean()), float(held.mean())
    r.better = int((gains > 1e-9).sum())
    r.worse = int((gains < -1e-9).sum())
    r.same = r.videos - r.better - r.worse
    r.p = luck_chance(gains)
    r.wins = bool(r.videos >= MIN_VIDEOS and gains.mean() >= MIN_LIFT and r.p <= MAX_P)   # stored as JSON
    return r


async def load_videos(session, labels: dict) -> list[Video]:
    """`labels` is evaluate.load_labels(): {project id: [(start, end), ...]}."""
    from sqlalchemy import select

    from app.models.project import Analysis, Project, ProjectEdit

    by_source: dict = {}
    q = (select(Project, Analysis.clips, Analysis.category)
         .join(Analysis, Analysis.project_id == Project.id).order_by(Project.created_at))
    for project, clips, category in (await session.execute(q)).all():
        moments = labels.get(project.id)
        edit = None if moments is not None else await session.get(ProjectEdit, project.id)
        pairs = project_pairs(
            clips or [],
            float(cf.category_profile(category or "").get("energy_weight", 1.0)),
            moments=moments,
            edits=(edit.state or {}).get("edits") if edit else None,
        )
        if not len(pairs):
            continue
        # one video per source: the same upload on both sides of the held-out test would be
        # testing on the training data
        key = project.source_bytes or project.source_filename
        seen = by_source.get(key)
        if seen:
            seen.pairs = np.vstack([seen.pairs, pairs])
            seen.hand_labelled = seen.hand_labelled or moments is not None
        else:
            by_source[key] = Video(project.name, pairs, moments is not None)
    return list(by_source.values())


def verdict(r: Result) -> str:
    if not r.videos:
        return ("Nothing to train on: no video has both a good and a bad pick yet. Label videos in "
                "Admin > Training, or press Skip on weak clips in the editor.")
    if r.wins:
        return "The learned weights win on videos they never saw. To use them, replace the line in kappacut.py with:"
    if r.videos < MIN_VIDEOS:
        return f"Too few videos to trust this ({r.videos} of {MIN_VIDEOS}): keep the current weights."
    return "Not a clear win on videos they never saw: keep the current weights."


def paste_line(r: Result) -> str | None:
    return f"SCORE_WEIGHTS = {r.weights}" if r.wins else None


def result_json(r: Result) -> dict:
    return {**asdict(r), "verdict": verdict(r), "paste": paste_line(r)}


def format_result(r: Result) -> str:
    lines = [f"Training data: {r.videos} of the {MIN_VIDEOS} videos needed ({r.hand_labelled} hand-labelled, "
             f"{r.videos - r.hand_labelled} from Keep/Skip), {r.pairs} good-versus-bad pairs."]
    if not r.videos:
        lines += ["", verdict(r)]
        return "\n".join(lines)
    if r.hand_accuracy is not None:
        lines += ["", "Each video scored by weights trained only on the other videos:",
                  f"  current weights  {100 * r.hand_accuracy:.0f}%",
                  f"  learned weights  {100 * r.learned_accuracy:.0f}%",
                  f"  videos better: {r.better}, worse: {r.worse}, same: {r.same}; "
                  f"chance this is luck: {100 * r.p:.0f}%"]
    lines += ["", f"  {'part':<10}{'current':>8}{'learned':>9}"]
    lines += [f"  {k:<10}{cf.SCORE_WEIGHTS[k]:>8}{r.weights[k]:>9}" for k in PARTS]
    lines += ["", verdict(r)]
    if r.wins:
        lines.append(f"  {paste_line(r)}")
    return "\n".join(lines)
