import type { CSSProperties, SyntheticEvent } from "react";
import type { ClipEdit } from "./types";

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

export function clipVisualStyle(look: ClipEdit["look"] | undefined, color: ClipEdit["color"] | undefined): ClipVisual {
  const vertical = look?.vertical ?? false;
  const mode = look?.mode ?? "crop";
  const blurFill = vertical && mode !== "crop";

  const cropStyle: CSSProperties = vertical
    ? {
        width: "min(320px, 46vh)",
        aspectRatio: "9 / 16",
        margin: "0 auto",
        overflow: "hidden",
        borderRadius: "var(--r-md)",
        position: "relative",
        background: "#000",
        boxShadow: "0 0 0 1px var(--border), var(--elev-2)",
      }
    : { borderRadius: "var(--r-md)", overflow: "hidden", background: "#000" };

  const bgStyle: CSSProperties = {
    position: "absolute",
    inset: 0,
    objectFit: "cover",
    transform: "scale(1.08)", // hide the soft edge the blur leaves at the frame border
    filter: `blur(${Math.max(2, look?.blur ?? 24)}px) brightness(.94) saturate(1.12)`,
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

  const innerStyle: CSSProperties = !vertical
    ? { width: "100%", display: "block", filter: colorFilter(color) }
    : mode === "fill"
      ? { position: "absolute", inset: 0, objectFit: "contain", filter: colorFilter(color) }
      : {
          position: "absolute",
          inset: 0,
          objectFit: "cover",
          objectPosition: `${(look?.px ?? 0.5) * 100}% ${(look?.py ?? 0.5) * 100}%`,
          transform: `scale(${look?.zoom ?? 1})`,
          filter: colorFilter(color),
        };

  return { vertical, mode, blurFill, cropStyle, bgStyle, fgWrapStyle, innerStyle };
}

export function colorFilter(c?: { preset: string; b: number; c: number; s: number }): string {
  if (!c) return "none";
  const parts: string[] = [];
  if (c.b) parts.push(`brightness(${1 + c.b})`);
  if (c.c !== 1) parts.push(`contrast(${c.c})`);
  if (c.s !== 1) parts.push(`saturate(${c.s})`);
  if (c.preset === "mono") parts.push("grayscale(1)");
  if (c.preset === "warm") parts.push("sepia(0.25)");
  if (c.preset === "cool") parts.push("hue-rotate(-8deg) saturate(1.08)");
  if (c.preset === "punchy") parts.push("contrast(1.12) saturate(1.18)");
  if (c.preset === "film") parts.push("contrast(1.06) saturate(0.92) sepia(0.08)");
  return parts.length ? parts.join(" ") : "none";
}
