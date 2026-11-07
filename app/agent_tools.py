"""What the editing agent can do: the tools it's offered, and the checks each one makes.

Workspace holds a copy of a project's edits and changes only that copy. Nothing here touches
the database or the network, so tests/test_agent.py can run every tool directly. A tool's
limits are the limits of the control it stands in for (frontend/src/editor/Inspector.tsx and
store.ts), so the agent can't reach a state the editor couldn't.

A tool that's asked for something impossible answers with {"error": ...} rather than raising:
the model reads that and can correct itself, or tell the person why not.
"""
from __future__ import annotations

import bisect
import copy
import inspect
import json
import math
import re

MIN_CLIP_SECONDS = 1.0      # what the trim handles and store.markIn/markOut keep
SNAP_SECONDS = 0.35         # a cut this close to a word's edge moves onto it, not through the word
TRANSCRIPT_WINDOW = 240     # seconds one read_transcript call can return
TRANSCRIPT_MARGIN = 20      # how far either side of a clip read_transcript shows by default
TRANSCRIPT_CHARS = 6000     # a hard stop on what one read can add to the prompt
SEARCH_HITS = 15
MAX_SOUNDS_PER_CLIP = 32    # app/projects.py MAX_SFX_PER_CLIP: a render drops any past this

CAPTION_STYLES = ("clean", "bold", "box", "pop")
CAPTION_POSITIONS = ("bottom", "center", "top")
CAPTION_ANIMS = ("none", "pop", "fade", "slide", "bounce")
CROP_MODES = ("crop", "fill", "cropfill")
COLOR_PRESETS = ("none", "punchy", "warm", "cool", "film", "mono")

# (lowest, highest) of each slider in the Inspector
SIZE = (0.7, 1.6)
ZOOM = (1.0, 2.2)
BLUR = (0.0, 60.0)
UNIT = (0.0, 1.0)
BRIGHTNESS = (-0.3, 0.3)
CONTRAST = (0.7, 1.4)
SATURATION = (0.0, 1.6)
GAIN = (-60.0, 12.0)


class ToolError(Exception):
    """Goes back to the model as {"error": ...}."""


# left out when comparing a search with what was said: they'd match nearly every line
_COMMON = frozenset("a an and are at be but by did do for from he her him his i in is it me my no not of on or our she "
                    "so that the their them they this to us was we were what when where who with you your".split())


_CJK_RUN = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")


def _keywords(text: str) -> list[str]:
    """The words of `text`, in any script. Chinese and Japanese have no spaces to find them by, so
    pairs of characters stand in for words."""
    text = text.lower()
    pairs = [run[i:i + 2] for run in _CJK_RUN.findall(text) for i in range(max(len(run) - 1, 1))]
    words = re.findall(r"(?:[\w']|[\u0300-\u036f\u0591-\u05c7\u0610-\u061a\u064b-\u065f\u0900-\u0dff\u0e00-\u0eff\u1000-\u109f\u1780-\u17ff])+", _CJK_RUN.sub(" ", text))
    return [w for w in words if w not in _COMMON] + pairs


def _same_word(a: str, b: str) -> bool:
    """"month" and "months" are the same word here, and so are "say" and "says", but "every" and
    "everything" aren't. "said" isn't either, but one miss in a phrase still leaves a close match."""
    return a == b or (min(len(a), len(b)) >= 3 and abs(len(a) - len(b)) <= 2 and (a.startswith(b) or b.startswith(a)))


def default_edit(clip: dict) -> dict:
    """frontend/src/editor/types.ts defaultEdit."""
    return {
        "keep": True,
        "start": float(clip.get("start_seconds") or 0),
        "end": float(clip.get("end_seconds") or 0),
        "caption": {"on": True, "style": "clean", "position": "bottom", "size": 1, "anim": "none", "karaoke": False},
        "look": {"vertical": False, "mode": "crop", "px": 0.5, "py": 0.42, "zoom": 1, "blur": 24},
        "color": {"preset": "none", "b": 0, "c": 1, "s": 1},
        "sfx": [],
        "sfxAuto": False,
    }


def normalize_edit(raw: object, clip: dict) -> dict:
    """The browser's copy of a clip's edit with anything missing filled in. The browser sends
    these, so nothing is assumed about their shape."""
    out = default_edit(clip)
    if not isinstance(raw, dict):
        return out
    for group in ("caption", "look", "color"):
        if isinstance(raw.get(group), dict):
            out[group] = {**out[group], **raw[group]}
    if isinstance(raw.get("sfx"), list):
        out["sfx"] = [s for s in raw["sfx"] if isinstance(s, dict)]
    for key in ("keep", "sfxAuto"):
        if isinstance(raw.get(key), bool):
            out[key] = raw[key]
    for key in ("start", "end"):
        value = raw.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            out[key] = float(value)
    return out


def face_frame(face: dict | None, mode: str, zoom: float) -> dict:
    """frontend/src/editor/types.ts faceFrame: px/py that centre the crop window on a face."""
    if not face:
        return {"px": 0.5, "py": 0.5}
    clamp = lambda v: max(0.0, min(1.0, v))   # noqa: E731
    ratio = face.get("ar") or 16 / 9
    w = min(1, (9 / 16 if mode == "crop" else 16 / 9) / ratio) / zoom
    h = 1 / zoom
    return {
        "px": 0.5 if w >= 0.999 else round(clamp((face["x"] - w / 2) / (1 - w)), 3),
        "py": 0.5 if h >= 0.999 else round(clamp((face["y"] - h / 2) / (1 - h)), 3),
    }


def _clock(seconds: float) -> str:
    seconds = max(0, round(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def _number(value: object, name: str) -> float:
    if isinstance(value, bool):
        raise ToolError(f"{name} must be a number.")
    try:
        n = float(value)            # models sometimes send "1.2"
    except (TypeError, ValueError):
        raise ToolError(f"{name} must be a number.") from None
    if not math.isfinite(n):
        raise ToolError(f"{name} must be a number.")
    return n


def _flag(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise ToolError(f"{name} must be true or false.")
    return value


def _choice(value: object, options: tuple, name: str) -> str:
    if value not in options:
        raise ToolError(f"{name} must be one of: {', '.join(options)}.")
    return value


def _flatten(edit: dict) -> dict:
    """Every field the agent can change, by the path the browser applies it at."""
    out = {"keep": edit["keep"], "start": edit["start"], "end": edit["end"], "sfx": edit["sfx"], "sfxAuto": edit["sfxAuto"]}
    for group, keys in (("caption", ("on", "style", "position", "size", "anim", "karaoke")),
                        ("look", ("vertical", "mode", "px", "py", "zoom", "blur")),
                        ("color", ("preset", "b", "c", "s"))):
        for key in keys:
            out[f"{group}.{key}"] = edit[group].get(key)
    out["look.follow"] = bool(edit["look"].get("follow"))      # absent and false are the same
    return out


def _same(a: object, b: object) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool) and not isinstance(b, bool):
        return abs(a - b) < 1e-6
    return a == b


def _describe(path_changes: dict, before: dict, after: dict) -> list[str]:
    """What changed on one clip, in words. Written from the edits themselves, so the list a person
    reads is true whatever the model says it did."""
    words = []
    if "keep" in path_changes:
        words.append("Kept" if after["keep"] else "Skipped")
    if "start" in path_changes or "end" in path_changes:
        words.append(f"Trimmed to {_clock(after['start'])}–{_clock(after['end'])} "
                     f"({round(after['end'] - after['start'])} s, was {round(before['end'] - before['start'])} s)")
    c = after["caption"]
    labels = {
        "caption.on": "Captions on" if c.get("on") else "Captions off",
        "caption.style": f"Caption style: {c.get('style')}",
        "caption.position": f"Captions at the {c.get('position')}",
        "caption.size": f"Caption size: {c.get('size')}×",
        "caption.anim": f"Caption animation: {c.get('anim')}",
        "caption.karaoke": "Karaoke on" if c.get("karaoke") else "Karaoke off",
    }
    look = after["look"]
    labels.update({
        "look.vertical": "Reframed to 9:16 vertical" if look.get("vertical") else "Back to the original framing",
        "look.mode": f"Framing: {dict(crop='Crop', fill='Blur fill', cropfill='Subject + blur').get(look.get('mode'), look.get('mode'))}",
        "look.zoom": f"Zoom: {look.get('zoom')}×",
        "look.blur": f"Background blur: {look.get('blur')}",
        "look.follow": "Following the face" if look.get("follow") else "Stopped following the face",
    })
    color = after["color"]
    labels.update({
        "color.preset": f"Colour: {color.get('preset')}",
        "color.b": f"Brightness: {color.get('b')}",
        "color.c": f"Contrast: {color.get('c')}",
        "color.s": f"Saturation: {color.get('s')}",
        "sfxAuto": "Whoosh on the entrance" if after["sfxAuto"] else "No whoosh on the entrance",
    })
    words += [text for path, text in labels.items() if path in path_changes]
    if "look.px" in path_changes or "look.py" in path_changes:
        words.append("Moved the framing")
    if "sfx" in path_changes:
        names = lambda edit: [f"{s.get('name') or s.get('file')} at {_clock(s.get('at') or 0)}" for s in edit["sfx"]]   # noqa: E731
        was, now = names(before), names(after)
        words += [f"Added {n}" for n in now if n not in was] + [f"Removed {n}" for n in was if n not in now]
    return words


class Workspace:
    """`mode` says where the agent is talking from. "editor": the panel beside a clip, which can
    only edit. "page": the AI agent page with a project open, which can also propose. "desk": that
    page with no project ready, which can only talk and read the person's own account."""

    def __init__(self, clips: list[dict], segments: list[dict], edits: dict, *, duration: float = 0,
                 open_clip: int | None = None, sounds: list[dict] | tuple = (), max_clip_seconds: float = 600,
                 mode: str = "editor", account: dict | None = None, project: dict | None = None):
        self.mode = mode
        self.account = account or {}
        self.project = project
        self.proposals: dict = {}
        self.clips = clips
        self.segments = segments
        self.edits = [normalize_edit((edits or {}).get(str(i)), clip) for i, clip in enumerate(clips)]
        self.before = copy.deepcopy(self.edits)
        self.open_clip = open_clip if isinstance(open_clip, int) and 0 <= open_clip < len(clips) else None
        self.sounds = list(sounds)
        self.max_clip_seconds = max_clip_seconds
        ends = [float(s.get("end") or 0) for s in segments] + [float(c.get("end_seconds") or 0) for c in clips]
        self.duration = float(duration or 0) or max(ends, default=0.0)
        starts, finishes = [], []
        for seg in segments:
            words = seg.get("words") or []
            starts += [float(w["start"]) for w in words] or [float(seg.get("start") or 0)]
            finishes += [float(w["end"]) for w in words] or [float(seg.get("end") or 0)]
        self._starts, self._ends = sorted(starts), sorted(finishes)
        self._tools = {tool["function"]["name"]: getattr(self, tool["function"]["name"]) for tool in self.offered_tools()}
        self._moves: set = set()

    def offered_tools(self) -> list[dict]:
        return {"editor": TOOLS, "page": TOOLS + ACCOUNT_TOOLS + PROPOSE_TOOLS, "desk": ACCOUNT_TOOLS + CHOICE_TOOLS}[self.mode]

    # ---- the agent's way in
    def run(self, name: str, raw_args: object) -> dict:
        handler = self._tools.get(name)
        if handler is None:
            return {"error": f"There's no tool called {name}."}
        try:
            args = json.loads(raw_args or "{}") if isinstance(raw_args, str) else raw_args
        except ValueError:
            return {"error": "Those arguments weren't valid JSON."}
        if not isinstance(args, dict):
            return {"error": "Arguments must be an object."}
        try:
            inspect.signature(handler).bind(**args)
        except TypeError as e:
            return {"error": f"Those arguments don't fit {name}: {e}."}
        was = json.dumps(self.edits, sort_keys=True) if name in WRITING_TOOLS else None
        try:
            result = handler(**args)
        except ToolError as e:
            return {"error": str(e)}
        # Analyses often start clips vertical with bold captions. Asked for that, the model still said
        # "now set", so it has to be told that it did nothing. This replaces the tool's own answer:
        # beside its "done", the note was ignored 6 times in 10 straight after the same change.
        if was is not None and was == json.dumps(self.edits, sort_keys=True) and "already_done" not in result:
            return {"done": False, "changed_nothing": "They were already like that, so nothing changed. Tell them they already were, and don't say \"now\" or that you changed anything."}
        return result

    # ---- what the person is told, and what the browser applies
    def ops(self) -> list[dict]:
        out = []
        for i, (was, now) in enumerate(zip(self.before, self.edits)):
            old, new = _flatten(was), _flatten(now)
            out += [{"clip": i, "path": path, "value": new[path]} for path in new if not _same(old[path], new[path])]
        return out

    def changes(self) -> list[dict]:
        out = []
        for i, (was, now) in enumerate(zip(self.before, self.edits)):
            old, new = _flatten(was), _flatten(now)
            changed = {path for path in new if not _same(old[path], new[path])}
            if changed:
                clip = self.clips[i]
                out.append({"clip": i, "title": clip.get("title") or clip.get("hook") or "Moment",
                            "items": _describe(changed, was, now)})
        return out

    def proposal_list(self) -> list[dict]:
        """What the page should offer the person. Nothing here has happened: each one is a card or a
        button, and only their click does anything."""
        out = []
        if "clips" in self.proposals:
            out.append({"type": "clips", "clips": self.proposals["clips"]})
        if "choices" in self.proposals:
            out.append({"type": "choices", "options": self.proposals["choices"]})
        if "render" in self.proposals:
            out.append({"type": "render"})
        return out

    def summary(self) -> str:
        """The clips as they stand, for the prompt. One line each: the model rarely needs more."""
        lines = []
        for i, (clip, e) in enumerate(zip(self.clips, self.edits)):
            caption = e["caption"]
            parts = [f"clip {i + 1}", f'"{(clip.get("title") or clip.get("hook") or "Moment")[:80]}"',
                     f"{e['start']:.1f}s to {e['end']:.1f}s ({e['end'] - e['start']:.0f}s long)",
                     "kept" if e["keep"] else "skipped",
                     f"captions {caption['style']}, {caption['position']}, size {caption['size']}" if caption["on"] else "captions off",
                     f"vertical, {e['look']['mode']}" if e["look"]["vertical"] else "original framing",
                     f"colour {e['color']['preset']}",
                     f"{len(e['sfx'])} sounds"]
            if clip.get("virality") is not None:
                parts.append(f"score {clip['virality']}")
            if self.mode == "page" and clip.get("why"):
                parts.append(f"why it was picked: {str(clip['why'])[:140]}")
            lines.append("- " + "; ".join(parts))
        return "\n".join(lines)

    # ---- which clips
    def _one(self, clip: object) -> int:
        found = self._targets(clip)
        if len(found) != 1:
            raise ToolError("This tool takes one clip at a time.")
        return found[0]

    def _targets(self, target: object) -> list[int]:
        if isinstance(target, (list, tuple)):
            target = ",".join(str(t) for t in target)
        text = str(target).strip().lower()
        everyone = list(range(len(self.clips)))
        if text in ("all", "every", "everything"):
            return everyone
        if text in ("kept", "skipped"):
            found = [i for i in everyone if self.edits[i]["keep"] == (text == "kept")]
            if not found:
                raise ToolError(f"No clips are {text} right now.")
            return found
        if text in ("open", "current", "this"):
            if self.open_clip is None:
                raise ToolError("No clip is open in the editor, so name one.")
            return [self.open_clip]
        found = []
        for piece in text.replace(" ", ",").split(","):
            if not piece:
                continue
            if not piece.lstrip("-").isdigit():
                raise ToolError(f'Use clip numbers, "all", "kept", "skipped" or "open", not "{piece}".')
            n = int(piece)
            if not 1 <= n <= len(self.clips):
                raise ToolError(f"There's no clip {n}: this video has {len(self.clips)}.")
            if n - 1 not in found:
                found.append(n - 1)
        if not found:
            raise ToolError("Say which clips.")
        return found

    # ---- reading
    def read_transcript(self, clip: object = None, start: object = None, end: object = None) -> dict:
        if clip is not None:
            e = self.edits[self._one(clip)]
            lo, hi = e["start"] - TRANSCRIPT_MARGIN, e["end"] + TRANSCRIPT_MARGIN
        elif start is not None and end is not None:
            lo, hi = _number(start, "start"), _number(end, "end")
        else:
            raise ToolError("Give a clip, or a start and an end in seconds.")
        hi = min(hi, lo + TRANSCRIPT_WINDOW)
        lines, used, cut = [], 0, False
        for seg in self.segments:
            if float(seg.get("end") or 0) < lo or float(seg.get("start") or 0) > hi:
                continue
            line = f"[{float(seg['start']):.1f}-{float(seg['end']):.1f}] {seg.get('text', '').strip()}"
            if used + len(line) > TRANSCRIPT_CHARS:
                cut = True
                break
            lines.append(line)
            used += len(line)
        return {"lines": lines, "truncated": cut}

    def search_transcript(self, query: object) -> dict:
        """Exact matches first, then lines sharing most of its words. People describe what was said
        ("where the bank says no"), and rarely quote it, so an exact search alone mostly finds
        nothing, and the model then asks them for the wording it could have looked up."""
        needle = str(query or "").strip().lower()
        if len(needle) < 2:
            raise ToolError("Search for at least 2 characters.")
        wanted = _keywords(needle)
        scored = []
        for order, seg in enumerate(self.segments):
            text = seg.get("text", "")
            if needle in text.lower():
                scored.append((2.0, order, seg))
            elif wanted:
                said = _keywords(text)
                share = sum(any(_same_word(w, s) for s in said) for w in wanted) / len(wanted)
                if share >= 0.5:
                    scored.append((share, order, seg))
        scored.sort(key=lambda hit: (-hit[0], hit[1]))
        return {"hits": [f"[{float(s['start']):.1f}-{float(s['end']):.1f}] {s.get('text', '').strip()}" for _, _, s in scored[:SEARCH_HITS]],
                "total": len(scored)}

    def list_sounds(self) -> dict:
        return {"sounds": [s["name"] for s in self.sounds]}

    def my_usage(self) -> dict:
        usage = self.account.get("usage")
        return _usage_words(usage) if usage else {"error": "Usage isn't available right now."}

    def list_projects(self) -> dict:
        return {"projects": self.account.get("projects") or []}

    # ---- proposing: these only put a card in front of the person
    def suggest_clips(self, clips: object) -> dict:
        # Clips are stored in time order, so a group word ("kept") would lose the ranking, and the
        # card would disagree with what the model says about it.
        if isinstance(clips, str) and not any(ch.isdigit() for ch in clips):
            raise ToolError("Name the clips by number, best first, like [3, 1].")
        found = self._targets(clips)
        self.proposals["clips"] = found
        return {"done": True, "shown": [i + 1 for i in found],
                "note": "The person now sees these with Keep and Skip buttons. Nothing has changed yet."}

    def propose_render(self) -> dict:
        kept = [i + 1 for i, e in enumerate(self.edits) if e["keep"]]
        if not kept:
            raise ToolError("Every clip is skipped, so there's nothing to render. Don't keep any yourself: tell them, and ask which ones to keep.")
        self.proposals["render"] = True
        return {"done": True, "would_render": kept,
                "note": "The person now sees a Render button. Nothing renders unless they press it, so don't say it has started."}

    def offer_choices(self, options: object) -> dict:
        if not isinstance(options, list) or not 2 <= len(options) <= 4:
            raise ToolError("Offer 2 to 4 answers.")
        tidy = [str(o).strip() for o in options]
        if any(not o or len(o) > 60 for o in tidy):
            raise ToolError("Each answer must be short: 60 characters at most.")
        self.proposals["choices"] = tidy
        return {"done": True, "note": "The person now sees these as buttons. Pressing one sends it as their next message."}

    # ---- changing
    def _snap(self, t: float, marks: list[float]) -> float:
        i = bisect.bisect_left(marks, t)
        near = min(marks[max(0, i - 1):i + 1], key=lambda m: abs(m - t), default=None)
        return near if near is not None and abs(near - t) <= SNAP_SECONDS else t

    def trim_clip(self, clip: object, start: object = None, end: object = None,
                  move_start: object = None, move_end: object = None) -> dict:
        if start is not None and move_start is not None or end is not None and move_end is not None:
            raise ToolError("Use start or move_start, and end or move_end, not both.")
        if start is None and end is None and move_start is None and move_end is None:
            raise ToolError("Say what to change: start, end, move_start or move_end.")
        i = self._one(clip)
        e = self.edits[i]
        # Setting a value twice is harmless, but a move adds up. gpt-oss-120b repeated about half of
        # its moves on the next step, after the first had worked, so "cut 3 seconds" cut 5.6.
        move = (i, move_start, move_end)
        if (move_start is not None or move_end is not None) and start is None and end is None:
            if move in self._moves:
                return {"done": True, "already_done": "This exact move was already made, so it wasn't repeated.",
                        "clip": i + 1, "start": e["start"], "end": e["end"], "seconds": round(e["end"] - e["start"], 1)}
            self._moves.add(move)
        new_start, new_end = e["start"], e["end"]
        if start is not None:
            new_start = _number(start, "start")
        elif move_start is not None:
            new_start += _number(move_start, "move_start")
        if end is not None:
            new_end = _number(end, "end")
        elif move_end is not None:
            new_end += _number(move_end, "move_end")
        if start is not None or move_start is not None:
            new_start = self._snap(max(0.0, new_start), self._starts)
        if end is not None or move_end is not None:
            new_end = self._snap(min(new_end, self.duration) if self.duration else new_end, self._ends)
        length = new_end - new_start
        if length < MIN_CLIP_SECONDS:
            self._moves.discard(move)       # it didn't happen, so it can be tried again
            hint = " move_start and move_end shift an edge by that many seconds: use start or end for an exact time." \
                if move_start is not None or move_end is not None else ""
            raise ToolError(f"That leaves clip {i + 1} at {length:.1f}s, and a clip must be at least "
                            f"{MIN_CLIP_SECONDS:g}s. It's {e['start']:.1f}s to {e['end']:.1f}s now.{hint}")
        if length > self.max_clip_seconds:
            self._moves.discard(move)
            raise ToolError(f"That makes clip {i + 1} {length:.0f}s long, and the longest a clip can render is "
                            f"{self.max_clip_seconds:g}s.")
        e["start"], e["end"] = round(new_start, 2), round(new_end, 2)
        return {"done": True, "clip": i + 1, "start": e["start"], "end": e["end"], "seconds": round(length, 1)}

    def set_keep(self, target: object, keep: object) -> dict:
        keep = _flag(keep, "keep")
        found = self._targets(target)
        for i in found:
            self.edits[i]["keep"] = keep
        return {"clips": [i + 1 for i in found], "keep": keep}

    def set_captions(self, target: object, on: object = None, style: object = None, position: object = None,
                     size: object = None, animation: object = None, karaoke: object = None) -> dict:
        new = {}
        if style is not None:
            new["style"] = _choice(style, CAPTION_STYLES, "style")
        if position is not None:
            new["position"] = _choice(position, CAPTION_POSITIONS, "position")
        if animation is not None:
            new["anim"] = _choice(animation, CAPTION_ANIMS, "animation")
        if size is not None:
            new["size"] = round(max(SIZE[0], min(SIZE[1], _number(size, "size"))), 2)
        if karaoke is not None:
            new["karaoke"] = _flag(karaoke, "karaoke")
        if on is not None:
            new["on"] = _flag(on, "on")
        elif new:
            new["on"] = True            # styling captions that are off would change nothing you can see
        if not new:
            raise ToolError("Say what to change about the captions.")
        found = self._targets(target)
        for i in found:
            self.edits[i]["caption"].update(new)
        return {"clips": [i + 1 for i in found], "captions": self.edits[found[0]]["caption"]}

    def set_framing(self, target: object, vertical: object = None, mode: object = None, zoom: object = None,
                    blur: object = None, follow_face: object = None, center_on_face: object = None,
                    px: object = None, py: object = None) -> dict:
        if all(v is None for v in (vertical, mode, zoom, blur, follow_face, center_on_face, px, py)):
            raise ToolError("Say what to change about the framing.")
        vertical = None if vertical is None else _flag(vertical, "vertical")
        mode = None if mode is None else _choice(mode, CROP_MODES, "mode")
        zoom = None if zoom is None else round(max(ZOOM[0], min(ZOOM[1], _number(zoom, "zoom"))), 2)
        blur = None if blur is None else round(max(BLUR[0], min(BLUR[1], _number(blur, "blur"))))
        follow_face = None if follow_face is None else _flag(follow_face, "follow_face")
        center_on_face = None if center_on_face is None else _flag(center_on_face, "center_on_face")
        px = None if px is None else max(UNIT[0], min(UNIT[1], _number(px, "px")))
        py = None if py is None else max(UNIT[0], min(UNIT[1], _number(py, "py")))
        found = self._targets(target)
        notes = []
        for i in found:
            face = self.clips[i].get("face")
            look = self.edits[i]["look"]
            was_vertical = look["vertical"]
            # the other controls only exist on a vertical clip, so using one means vertical
            look["vertical"] = vertical if vertical is not None else True
            turning_on = look["vertical"] and not was_vertical
            # Crop has no background to blur, so a blur there would change nothing you can see
            wanted_mode = mode or ("cropfill" if blur is not None and look["mode"] == "crop" else None)
            if wanted_mode is not None and wanted_mode != look["mode"]:
                # as the mode cards do: start the new mode on the face, zoomed out
                look["mode"], look["zoom"] = wanted_mode, 1
                look.update(face_frame(face, wanted_mode, 1))
            elif turning_on and face:
                look.update(face_frame(face, look["mode"], look["zoom"]))
            if zoom is not None:
                look["zoom"] = zoom
            if blur is not None:
                look["blur"] = blur
            if center_on_face:
                if face:
                    look.update(face_frame(face, look["mode"], look["zoom"]))
                    look["follow"] = False
                else:
                    notes.append(f"Clip {i + 1} has no detected face to centre on.")
            if px is not None or py is not None:
                if px is not None:
                    look["px"] = round(px, 3)
                if py is not None:
                    look["py"] = round(py, 3)
                look["follow"] = False
            if follow_face is not None:
                if follow_face and not (face and face.get("track")):
                    notes.append(f"Clip {i + 1} has no face path to follow.")
                else:
                    look["follow"] = follow_face
        out = {"clips": [i + 1 for i in found], "framing": self.edits[found[0]]["look"]}
        if notes:
            out["notes"] = notes
        return out

    def set_color(self, target: object, preset: object = None, brightness: object = None, contrast: object = None,
                  saturation: object = None, reset: object = None) -> dict:
        new = {}
        if reset is not None and _flag(reset, "reset"):
            new = {"preset": "none", "b": 0, "c": 1, "s": 1}
        if preset is not None:
            new["preset"] = _choice(preset, COLOR_PRESETS, "preset")
        if brightness is not None:
            new["b"] = round(max(BRIGHTNESS[0], min(BRIGHTNESS[1], _number(brightness, "brightness"))), 2)
        if contrast is not None:
            new["c"] = round(max(CONTRAST[0], min(CONTRAST[1], _number(contrast, "contrast"))), 2)
        if saturation is not None:
            new["s"] = round(max(SATURATION[0], min(SATURATION[1], _number(saturation, "saturation"))), 2)
        if not new:
            raise ToolError("Say what to change about the colour.")
        found = self._targets(target)
        for i in found:
            self.edits[i]["color"].update(new)
        return {"clips": [i + 1 for i in found], "colour": self.edits[found[0]]["color"]}

    def _sound(self, name: object) -> dict:
        wanted = str(name or "").strip().lower()
        for sound in self.sounds:
            if wanted in (sound["name"].lower(), sound["file"].lower()):
                return sound
        options = ", ".join(s["name"] for s in self.sounds) or "none"
        raise ToolError(f'There\'s no sound called "{name}". Available: {options}.')

    def add_sound(self, target: object, sound: object, at: object = 0, gain: object = -3) -> dict:
        """`at` is seconds from the start of the clip, as the editor's sound list counts it."""
        found_sound = self._sound(sound)
        at = _number(at, "at")
        gain = max(GAIN[0], min(GAIN[1], _number(gain, "gain")))
        found = self._targets(target)
        for i in found:
            e = self.edits[i]
            if not 0 <= at < e["end"] - e["start"]:
                raise ToolError(f"Clip {i + 1} is {e['end'] - e['start']:.1f}s long, so a sound can't go at {at:g}s.")
            if len(e["sfx"]) >= MAX_SOUNDS_PER_CLIP:
                raise ToolError(f"Clip {i + 1} already has the most sounds a clip can have.")
        for i in found:
            e = self.edits[i]
            kept = [s for s in e["sfx"] if not (s.get("file") == found_sound["file"] and abs(float(s.get("at") or 0) - at) < 0.05)]
            e["sfx"] = kept + [{"file": found_sound["file"], "name": found_sound["name"], "at": round(at, 2), "gain": gain}]
        return {"clips": [i + 1 for i in found], "sound": found_sound["name"], "at": round(at, 2)}

    def remove_sounds(self, target: object, sound: object = None) -> dict:
        wanted = None if sound is None else str(sound).strip().lower()
        found = self._targets(target)
        removed = 0
        for i in found:
            e = self.edits[i]
            kept = [s for s in e["sfx"]
                    if wanted is not None and wanted not in (str(s.get("name")).lower(), str(s.get("file")).lower())]
            removed += len(e["sfx"]) - len(kept)
            e["sfx"] = kept
        return {"clips": [i + 1 for i in found], "removed": removed}

    def set_entrance_whoosh(self, target: object, on: object) -> dict:
        on = _flag(on, "on")
        found = self._targets(target)
        for i in found:
            self.edits[i]["sfxAuto"] = on
        return {"clips": [i + 1 for i in found], "on": on}


def _optional(prop: dict) -> dict:
    """Also allows null. Groq checks every tool call against the schema and refuses a mismatch before
    we see it, and gpt-oss writes `"sound": null` for "no particular sound": without this, 10 of 12
    tries at "remove the sound effects" were refused. The tools treat None as not given."""
    kinds = prop["type"] if isinstance(prop["type"], list) else [prop["type"]]
    out = {**prop, "type": [*kinds, "null"]}
    if "enum" in prop:
        out["enum"] = [*prop["enum"], None]
    return out


def _tool(name: str, description: str, properties: dict, required: tuple = ()) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "required": list(required), "additionalProperties": False,
                       "properties": {k: (v if k in required else _optional(v)) for k, v in properties.items()}},
    }}


WRITING_TOOLS = {"trim_clip", "set_keep", "set_captions", "set_framing", "set_color", "add_sound", "remove_sounds", "set_entrance_whoosh"}


def _usage_words(usage: dict) -> dict:
    """Finished answers in the units people use, so the model has nothing to work out: given 1200
    of 5120 MB, gpt-oss-120b said "3 GB used of 5 GB"."""
    def line(used: float, cap: float, unit: str = "") -> str:
        used = round(used, 1)
        if not usage.get("has_limits", True):
            return f"{used:g}{unit} used, and this account has no limit"
        return f"{used:g}{unit} of {round(cap, 1):g}{unit} used, {max(round(cap - used, 1), 0):g}{unit} left"

    out = {}
    for key, value in usage.items():
        if key == "storage_mb":
            out["storage"] = line(value["used"] / 1024, value["cap"] / 1024, " GB")
        elif isinstance(value, dict):
            out[key] = line(value["used"], value["cap"])
    return out


_TARGET = {"type": ["string", "integer", "array"], "items": {"type": ["string", "integer"]},
           "description": 'Which clips: a number ("3"), several ("1,2,5"), "all", "kept", "skipped", or "open" for the clip open in the editor.'}
_CLIP = {"type": ["string", "integer"], "description": 'One clip: its number, or "open" for the clip open in the editor.'}
_BOOL = {"type": "boolean"}


def _ranged(low: float, high: float, what: str) -> dict:
    return {"type": "number", "description": f"{what} From {low:g} to {high:g}."}


TOOLS = [
    _tool("read_transcript", "Read what's said, with times in seconds of the source video. Give a clip to see it with some context either side, or a start and end.",
          {"clip": _CLIP, "start": {"type": "number"}, "end": {"type": "number"}}),
    _tool("search_transcript", "Find where a word or phrase is said in the whole video.", {"query": {"type": "string"}}, ("query",)),
    _tool("list_sounds", "List the sound effects that can be added.", {}),
    _tool("trim_clip", "Change where one clip starts or ends. start and end are seconds in the source video. move_start and move_end shift an edge by seconds: positive is later, negative is earlier, so cutting 3 seconds off the beginning is move_start 3, and cutting 3 off the end is move_end -3.",
          {"clip": _CLIP, "start": {"type": "number"}, "end": {"type": "number"},
           "move_start": {"type": "number"}, "move_end": {"type": "number"}}, ("clip",)),
    _tool("set_keep", "Keep clips or skip them. Skipped clips aren't rendered, and can be kept again later.",
          {"target": _TARGET, "keep": _BOOL}, ("target", "keep")),
    _tool("set_captions", "Change the burned-in captions. Styling them also turns them on. \"Pop captions\" or \"bold captions\" means style; animation is only how they appear.",
          {"target": _TARGET, "on": _BOOL, "style": {"type": "string", "enum": list(CAPTION_STYLES)},
           "position": {"type": "string", "enum": list(CAPTION_POSITIONS)},
           "size": _ranged(*SIZE, "Size multiplier; 1 is normal."),
           "animation": {"type": "string", "enum": list(CAPTION_ANIMS)},
           "karaoke": {"type": "boolean", "description": "Highlight each word as it's said."}}, ("target",)),
    _tool("set_framing", "Change how the picture is framed. vertical true reframes to 9:16 for phones. mode, with the names the editor shows: crop (Crop) fills the frame with a cut-out of the picture, fill (Blur fill) shows the whole picture over a blurred copy, cropfill (Subject + blur) shows the subject large over a blurred copy.",
          {"target": _TARGET, "vertical": _BOOL, "mode": {"type": "string", "enum": list(CROP_MODES)},
           "zoom": _ranged(*ZOOM, "Zoom; 1 is none."), "blur": _ranged(*BLUR, "Background blur amount. Not needed just to get a blurred background: choose mode fill or cropfill for that."),
           "follow_face": {"type": "boolean", "description": "Pan with the person's face over time."},
           "center_on_face": {"type": "boolean", "description": "Fix the frame on the person's usual position."},
           "px": _ranged(*UNIT, "Horizontal position; 0 is the far left."),
           "py": _ranged(*UNIT, "Vertical position; 0 is the top.")}, ("target",)),
    _tool("set_color", "Change the colour. Presets: punchy (vivid), warm, cool, film (muted, slightly warm), mono (black and white), none. reset true puts everything back to normal.",
          {"target": _TARGET, "preset": {"type": "string", "enum": list(COLOR_PRESETS)},
           "brightness": _ranged(*BRIGHTNESS, "0 is normal."), "contrast": _ranged(*CONTRAST, "1 is normal."),
           "saturation": _ranged(*SATURATION, "1 is normal; 0 is grey."), "reset": _BOOL}, ("target",)),
    _tool("add_sound", "Add a sound effect by name, from list_sounds. at is seconds from the start of the clip (not the source video).",
          {"target": _TARGET, "sound": {"type": "string"}, "at": {"type": "number"},
           "gain": _ranged(*GAIN, "Loudness in decibels; -3 is the usual level.")}, ("target", "sound")),
    _tool("remove_sounds", "Remove sound effects from clips: one by name, or all of them if no name is given.",
          {"target": _TARGET, "sound": {"type": "string"}}, ("target",)),
    _tool("set_entrance_whoosh", "Turn the whoosh on a clip's entrance on or off.", {"target": _TARGET, "on": _BOOL}, ("target", "on")),
]

ACCOUNT_TOOLS = [
    _tool("my_usage", "What this person has used and what they have left: minutes, storage, clips today and projects. Already worked out, so repeat the numbers as given.", {}),
    _tool("list_projects", "This person's projects, newest first, with status, how many clips and how many rendered.", {}),
]
CHOICE_TOOLS = [
    _tool("offer_choices", "Show 2 to 4 short answers as buttons, for a yes-or-no or this-or-that question you just asked. Pressing one sends it as their next message.",
          {"options": {"type": "array", "items": {"type": "string"}}}, ("options",)),
]
PROPOSE_TOOLS = CHOICE_TOOLS + [
    _tool("suggest_clips", "Recommend clips, best first: shows them to the person with their score and reason, and Keep and Skip buttons. It changes nothing. Use it whenever they ask which clips to use or post.",
          {"clips": {"type": "array", "items": {"type": "integer"},
                     "description": "Clip numbers, best first. Only the ones you'd pick: one if they asked for the best one, three if they asked for three."}}, ("clips",)),
    _tool("propose_render", "Show a Render button for the clips currently kept, and what that would use. It doesn't render: only the person's click does. Use it when they want to render or export.", {}),
]
