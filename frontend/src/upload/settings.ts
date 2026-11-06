export type Platform = "vertical" | "shorts" | "general" | "";
export type Vibe = "reaction" | "fail" | "bit" | "story" | "quote" | "wholesome" | "";
export type Length = "short" | "medium" | "long" | "";
export type Count = "3" | "5" | "10" | "";
// must match the [category.<name>] tables in categories.toml; "" = detect automatically
export type ContentType = "" | "gaming" | "reaction" | "food" | "comedy" | "podcast" | "interview" | "tutorial" | "vlog" | "sports";

export const PLATFORMS: { id: Exclude<Platform, "">; label: string }[] = [
  { id: "vertical", label: "Vertical (TikTok/Reels)" },
  { id: "shorts", label: "YouTube Shorts" },
  { id: "general", label: "General" },
];
export const VIBES: { id: Exclude<Vibe, "">; label: string }[] = [
  { id: "reaction", label: "Reaction" },
  { id: "fail", label: "Fail" },
  { id: "bit", label: "Bit" },
  { id: "story", label: "Story" },
  { id: "quote", label: "Quote" },
  { id: "wholesome", label: "Wholesome" },
];
export const LENGTHS: { id: Exclude<Length, "">; label: string }[] = [
  { id: "short", label: "<15s" },
  { id: "medium", label: "15-30s" },
  { id: "long", label: "30-60s" },
];
const LENGTH_TEXT: Record<Exclude<Length, "">, string> = { short: "under 15s", medium: "15-30s", long: "30-60s" };
export const COUNTS: { id: Exclude<Count, "">; label: string }[] = [
  { id: "3", label: "3" },
  { id: "5", label: "5" },
  { id: "10", label: "10" },
];
export const CONTENT_TYPES: { id: ContentType; label: string }[] = [
  { id: "", label: "Auto" },
  { id: "gaming", label: "Gaming" },
  { id: "reaction", label: "Reaction" },
  { id: "food", label: "Food / challenge" },
  { id: "comedy", label: "Comedy" },
  { id: "podcast", label: "Podcast" },
  { id: "interview", label: "Interview" },
  { id: "tutorial", label: "Tutorial" },
  { id: "vlog", label: "Vlog" },
  { id: "sports", label: "Sports" },
];

export interface ClipSettings {
  type: ContentType;
  platform: Platform;
  vibe: Vibe;
  length: Length;
  count: Count;
  avoid: string;
  /** on: stop at the editor for review. off: render automatically once picks are ready */
  review: boolean;
}

export const DEFAULT_SETTINGS: ClipSettings = { type: "", platform: "", vibe: "", length: "", count: "", avoid: "", review: true };

/** What the picker is told to aim for. Content type and clip count go as their own fields. */
export function composeTaste(s: ClipSettings, ask?: string): string {
  const parts: string[] = [];
  if (s.vibe) parts.push(`Prioritize beat_type: ${s.vibe}.`);
  if (s.length) parts.push(`Target length ${LENGTH_TEXT[s.length]}.`);
  if (s.platform) parts.push(`Format: ${PLATFORMS.find((p) => p.id === s.platform)?.label}.`);
  if (s.avoid.trim()) parts.push(`Avoid: ${s.avoid.trim()}.`);
  if (ask?.trim()) parts.push(`The person asked: ${ask.trim()}`);
  return parts.join(" ");
}

/** The line under the drop box, so the settings in use are always in view. */
export function summarize(s: ClipSettings, short: boolean): string {
  const type = CONTENT_TYPES.find((c) => c.id === s.type)?.label ?? "Auto";
  const review = `Review before rendering: ${s.review ? "On" : "Off"}`;
  if (short) return `${type}  ·  ${review}`;
  const parts = [`Content type: ${type}`];
  if (s.platform) parts.push(PLATFORMS.find((p) => p.id === s.platform)?.label ?? "");
  if (s.vibe) parts.push(`Vibe: ${VIBES.find((v) => v.id === s.vibe)?.label}`);
  if (s.length) parts.push(`Length: ${LENGTHS.find((l) => l.id === s.length)?.label}`);
  parts.push(s.count ? `Clips: up to ${s.count}` : "Clips: as many strong ones as it finds");
  parts.push(review);
  return parts.join("  ·  ");
}
