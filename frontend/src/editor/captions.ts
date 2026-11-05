import type { Segment } from "@/api/types";
import type { ClipEdit } from "./types";

/* What the editor preview shows for a clip's captions: a copy of what pipeline.py burns into
 * the render (caption_lines, _chunk_words, build_ass, CAP_STYLES). Change one, change both,
 * or the preview stops telling the truth about the clip. */

const KAR_MAX_WORDS = 5;
const KAR_MAX_SPAN = 2.6;

/** Sizes are in pixels of the render's frame, 1080 wide (1920 for a horizontal clip). */
export const CAP_STYLES = {
  clean: { size: 52, outline: 2.4, box: false, color: "#ffffff", stroke: "#000000", hot: "#fff033" },
  bold: { size: 62, outline: 4, box: false, color: "#ffffff", stroke: "#000000", hot: "#fff033" },
  box: { size: 50, outline: 8, box: true, color: "#ffffff", stroke: "#000000", hot: "#fff033" },
  pop: { size: 68, outline: 5, box: false, color: "#fff033", stroke: "#101010", hot: "#ffffff" },
} as const;

/** One caption on screen: a transcript line, or one step of a karaoke group. */
export interface CaptionCue {
  /** Stays the same across a karaoke group's steps, so its entrance only plays once. */
  key: string;
  start: number;
  end: number;
  words: string[];
  /** The karaoke word lit up right now, or -1. */
  hot: number;
}

interface Word {
  w: string;
  start: number;
  end: number;
}

function cleanText(t: string | undefined, upper: boolean): string {
  const s = (t ?? "").replace(/\{/g, "(").replace(/\}/g, ")").replace(/\n/g, " ").trim();
  return upper ? s.toUpperCase() : s;
}

function chunkWords(words: Word[]): Word[][] {
  const sorted = [...words].sort((a, b) => a.start - b.start);
  const groups: Word[][] = [];
  let cur: Word[] = [];
  for (const w of sorted) {
    cur.push(w);
    const full = cur.length >= KAR_MAX_WORDS || w.end - cur[0].start > KAR_MAX_SPAN;
    if (full || (/[.!?]$/.test(w.w) && cur.length >= 3)) {
      groups.push(cur);
      cur = [];
    }
  }
  if (cur.length) groups.push(cur);
  const merged: Word[][] = [];                // fold tiny trailing groups back
  for (const g of groups) {
    const last = merged[merged.length - 1];
    if (last && g.length <= 1 && last.length <= KAR_MAX_WORDS) last.push(...g);
    else merged.push([...g]);
  }
  return merged;
}

/** Clip-local and rounded like the render's caption_lines, so a group can't split elsewhere. */
const local = (t: number, start: number) => Math.round((t - start) * 100) / 100;

/** Every caption of the clip [start, end), in source-video seconds. */
export function buildCues(
  segments: Segment[],
  start: number,
  end: number,
  caption: Pick<ClipEdit["caption"], "style" | "karaoke">,
): CaptionCue[] {
  const upper = caption.style === "pop";
  const cues: CaptionCue[] = [];
  segments.forEach((sg, li) => {
    const a = Number(sg.start);
    const b = Number(sg.end);
    if (b <= start || a >= end) return;
    const words: Word[] = caption.karaoke
      ? (sg.words ?? [])
          .filter((w) => w.end > start && w.start < end && w.w?.trim())
          .map((w) => ({
            w: w.w.trim(),
            start: local(Math.max(w.start, start), start),
            end: local(Math.min(w.end, end), start),
          }))
      : [];
    if (words.length) {
      chunkWords(words).forEach((g, gi) => {
        const texts = g.map((w) => cleanText(w.w, upper));
        const groupEnd = g[g.length - 1].end;
        g.forEach((w, i) => {
          const next = i + 1 < g.length ? g[i + 1].start : groupEnd;
          cues.push({
            key: `${li}:${gi}`,
            start: start + w.start,
            end: start + Math.max(next, w.start + 0.05),
            words: texts,
            hot: i,
          });
        });
      });
    } else {
      const text = cleanText(sg.text, upper);
      if (text) {
        const from = local(Math.max(a, start), start);
        const to = local(Math.min(b, end), start);
        cues.push({
          key: `${li}`,
          start: start + from,
          end: start + Math.max(to, from + 0.05),
          words: [text],
          hot: -1,
        });
      }
    }
  });
  return cues;
}

export function cueAt(cues: CaptionCue[], t: number): CaptionCue | null {
  return cues.find((c) => t >= c.start && t < c.end) ?? null;
}
