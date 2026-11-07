import type { CSSProperties, SyntheticEvent } from "react";
import type { ClipEdit } from "./types";
import { colorMatrix } from "./colorGrade";

/** Keep a <video> seeked to and looping within [start,end) — for the always-on blurred
 * backdrop layer, which must actually decode/paint frames rather than sit on a single
 * paused seek (unreliable across browsers until playback actually runs). */
export function clipLoopHandlers(start: number, end: number) {
  return {
    onLoadedMetadata: (e: SyntheticEvent<HTMLVideoElement>) => {
      try {
        e.currentTarget.currentTime = start;
      } catch {
        /* metadata not ready */
      }
    },
    onTimeUpdate: (e: SyntheticEvent<HTMLVideoElement>) => {
      const t = e.currentTarget.currentTime;
      if (t >= end - 0.03 || t < start - 0.03) {
        try {
          e.currentTarget.currentTime = start;
        } catch {
          /* ignore */
        }
      }
    },
  };
}

/** A <video> is a replaced element: `inset: 0` alone leaves it at its intrinsic size
 * (e.g. 1280x720) instead of stretching, so every layered video needs explicit 100%. */
const FILL: CSSProperties = {
  position: "absolute",
  inset: 0,
  width: "100%",
  height: "100%",
  maxWidth: "none",
};

export interface ClipVisual {
  vertical: boolean;
  mode: ClipEdit["look"]["mode"];
  /** "fill"/"cropfill" leave bars the render fills with a blurred copy of the video
   * (see cut_clip's `[bg]`/`[fg]` overlay) — mirrored here via bgStyle + fgWrapStyle. */
  blurFill: boolean;
  cropStyle: CSSProperties;
  bgStyle: CSSProperties;
  fgWrapStyle: CSSProperties;
  innerStyle: CSSProperties;
}

/** `gradeId`: the id of a mounted <ColorGrade> (editor/colorGrade.tsx) for this clip's colour. */
export function clipVisualStyle(
  look: ClipEdit["look"] | undefined,
  color: ClipEdit["color"] | undefined,
  gradeId?: string,
): ClipVisual {
  // the same matrix the render applies; "none" when the look is neutral
  const grade = gradeId && colorMatrix(color) ? `url(#${gradeId})` : "none";
  const vertical = look?.vertical ?? false;
  const mode = look?.mode ?? "crop";
  const blurFill = vertical && mode !== "crop";

  const cropStyle: CSSProperties = vertical
    ? {
        // fill the container but never exceed it — a fixed 320px spilled out of the
        // ~220px review cards and over their neighbours
        width: "100%",
        maxWidth: "min(320px, 46vh)",
        aspectRatio: "9 / 16",
        // lets the blur below be sized in cqw, i.e. relative to this frame's width
        containerType: "inline-size",
        margin: "0 auto",
        overflow: "hidden",
        borderRadius: "var(--r-md)",
        position: "relative",
        background: "#000",
        boxShadow: "0 0 0 1px var(--border), var(--elev-2)",
      }
    : {
        borderRadius: "var(--r-md)",
        overflow: "hidden",
        background: "#000",
        // the caption overlay is positioned in this frame and sized in cqw of it
        position: "relative",
        containerType: "inline-size",
      };

  // The render's gblur sigma is in 1080px-wide output pixels; this frame is ~230-320px wide,
  // so the same number as CSS px blurred the preview 3-4x harder than the real clip.
  const sigma = Math.max(2, look?.blur ?? 24);
  const bgStyle: CSSProperties = {
    ...FILL,
    objectFit: "cover",
    transform: "scale(1.08)", // hide the soft edge the blur leaves at the frame border
    filter: `blur(calc(${sigma} * 100cqw / 1080)) brightness(.94) saturate(1.12)`,
  };

  // "cropfill" frames a subject rectangle (fixed 16:9, panned/zoomed) that only covers part
  // of the 9:16 frame, same as the render's `[fg]` box — the rest is the blurred bg layer.
  const fgWrapStyle: CSSProperties = {
    position: "absolute",
    left: 0,
    right: 0,
    top: "50%",
    transform: "translateY(-50%)",
    aspectRatio: "16 / 9",
    overflow: "hidden",
  };

  // crop / cropfill mirror cut_clip's window: a (1/zoom)-sized crop at x=(iw-w)*px,
  // y=(ih-h)*py. object-position places the cover crop, and scaling about the *same*
  // px/py point (not the centre) is exactly that window — with a centred origin the
  // Horizontal/Vertical sliders did nothing in the preview once the frame was filled.
  const px = (look?.px ?? 0.5) * 100;
  const py = (look?.py ?? 0.5) * 100;
  const innerStyle: CSSProperties = !vertical
    ? { width: "100%", display: "block", filter: grade }
    : mode === "fill"
      ? { ...FILL, objectFit: "contain", filter: grade }
      : {
          ...FILL,
          objectFit: "cover",
          objectPosition: `${px}% ${py}%`,
          transform: `scale(${look?.zoom ?? 1})`,
          transformOrigin: `${px}% ${py}%`,
          filter: grade,
        };

  return { vertical, mode, blurFill, cropStyle, bgStyle, fgWrapStyle, innerStyle };
}
