import presetsJson from "./colorPresets.json";

/**
 * The editor half of the shared colour grade. Mirrors kappacut.color_matrix step for step:
 * presets from colorPresets.json (the one definition both sides read), then the sliders,
 * composed into a 3x4 affine matrix on gamma-encoded RGB. The render applies the same matrix
 * with ffmpeg's colorchannelmixer; here it is an SVG feColorMatrix with color-interpolation-filters="sRGB"
 * (the SVG default, linearRGB, would not match ffmpeg's gamma-encoded maths).
 */
type Row = [number, number, number, number];
type Matrix = [Row, Row, Row];
export interface ColorLook { preset: string; b: number; c: number; s: number }

const LUMA = [0.2126, 0.7152, 0.0722] as const;
const IDENTITY: Matrix = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0]];
const PRESETS = Object.fromEntries(
  Object.entries(presetsJson as Record<string, unknown>).filter(([k, v]) => !k.startsWith("_") && Array.isArray(v)),
) as Record<string, [string, number][]>;

function opMatrix(op: string, v: number): Matrix {
  const [r, g, b] = LUMA;
  switch (op) {
    case "temp":
      return [[1 + v, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1 - v, 0]];
    case "saturate":
      return [
        [r + (1 - r) * v, g - g * v, b - b * v, 0],
        [r - r * v, g + (1 - g) * v, b - b * v, 0],
        [r - r * v, g - g * v, b + (1 - b) * v, 0],
      ];
    case "contrast": {
      const o = 0.5 * (1 - v);
      return [[v, 0, 0, o], [0, v, 0, o], [0, 0, v, o]];
    }
    case "brightness":
      return [[v, 0, 0, 0], [0, v, 0, 0], [0, 0, v, 0]];
    default:
      return IDENTITY;
  }
}

/** The affine map "apply a, then b". */
function then(a: Matrix, b: Matrix): Matrix {
  return [0, 1, 2].map((i) => [
    ...[0, 1, 2].map((j) => b[i][0] * a[0][j] + b[i][1] * a[1][j] + b[i][2] * a[2][j]),
    b[i][0] * a[0][3] + b[i][1] * a[1][3] + b[i][2] * a[2][3] + b[i][3],
  ]) as Matrix;
}

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));

export function colorMatrix(c?: ColorLook): Matrix | null {
  if (!c) return null;
  let m = IDENTITY;
  let touched = false;
  for (const [op, v] of PRESETS[(c.preset || "none").toLowerCase()] ?? []) {
    m = then(m, opMatrix(op, v));
    touched = true;
  }
  const steps: [string, number][] = [
    ["brightness", 1 + clamp(c.b ?? 0, -0.4, 0.4)],
    ["contrast", clamp(c.c ?? 1, 0.5, 1.8)],
    ["saturate", clamp(c.s ?? 1, 0, 2)],
  ];
  for (const [op, v] of steps) {
    if (Math.abs(v - 1) > 0.005) {
      m = then(m, opMatrix(op, v));
      touched = true;
    }
  }
  return touched ? m : null;
}

/** A filter id that is safe inside CSS url(#…) - React's useId() contains colons. */
export function gradeId(reactId: string): string {
  return "cg" + reactId.replace(/[^a-zA-Z0-9_-]/g, "");
}

/** Invisible SVG holding the grade as a filter; style a video with `filter: url(#id)`. */
export function ColorGrade({ id, color }: { id: string; color?: ColorLook }) {
  const m = colorMatrix(color);
  if (!m) return null;
  // feColorMatrix rows are [r g b a offset]; ours are [r g b offset] - alpha stays untouched
  const values =
    m.map(([r, g, b, o]) => [r, g, b, 0, o].map((v) => v.toFixed(5)).join(" ")).join(" ") + " 0 0 0 1 0";
  return (
    <svg aria-hidden width="0" height="0" style={{ position: "absolute", width: 0, height: 0, overflow: "hidden" }}>
      <filter id={id} colorInterpolationFilters="sRGB">
        <feColorMatrix type="matrix" values={values} />
      </filter>
    </svg>
  );
}
