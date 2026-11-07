# Design system rules

For turning Figma designs into Kappacut's frontend, and for building new UI. The code is the source
of truth: if Figma and these files disagree, say so instead of guessing.

Figma file: https://www.figma.com/design/tJNeIjH4tXsQhqnI4sTEvn (Upload dashboard canvas, and a Kit
page whose variables carry the same names as the CSS).

## Stack

React 19, TypeScript, Vite, React Router 7, TanStack Query, zustand. Code is in `frontend/src`, and
`@/` points there. There's no Tailwind, no CSS modules and no component library, so don't add one.
Check a change with `npm run build` in `frontend/`, which type-checks first.

## Styling

- Tokens: `src/styles/tokens.css`. Shared classes: `src/styles/global.css`.
- Reuse a class in `global.css` first. Put a new reusable pattern there too, with a short prefix
  (`.agent-*`, `.admin-*`, `.project-*`). Use inline `style` only for one-off layout, with tokens:
  `style={{ gap: "var(--sp-3)" }}`.
- Never hardcode a colour, spacing, radius or font size when a token exists. Map Figma values to
  `var(--…)`: the variable `status/danger` is `var(--danger)`, and `spacing/sp-3` is `var(--sp-3)`.

## Colour

There is one dark theme and no light theme. Black and white do almost all of the work.

- Selected or primary: white (`--accent`, on `--on-accent` text, with `--accent-soft` behind a selected chip).
- `--signal` (cyan) only marks something happening right now: a running job, the playhead, a tracked face.
- `--good`, `--amber` and `--danger` only when they mean something, and never as the only signal: pair
  them with a word or an icon.
- Grounds: `--bg` for the page, `--surface` for panels, `--glass-2` for raised controls. Borders:
  `--border`, or `--border-strong` when it needs to show.

## Type

- Space Grotesk: headings, card titles, and uppercase labels (`.eyebrow`, `Row` labels, status pills).
- Instrument Sans: everything else.
- Sizes are `--fs-2xs` (10.5) to `--fs-2xl` (28). Most of the interface is `--fs-xs` and `--fs-sm`.
- Figma has no Space Grotesk 600, so the kit's headings use Bold. In code, keep the weights already
  in `global.css`.

## Layout

- `AppShell` centres pages in `.wrap`, 1120px wide at most.
- Spacing is `--sp-1` to `--sp-6` (4 to 32). Corners are `--r-xs` to `--r-lg` (8 to 20) and `--r-pill`.
- Breakpoints in use: 1180, 900, 860 and 640px, all `max-width`.
- Every page has to work at 390px wide without the page scrolling sideways. Wide tables scroll inside
  `.table-wrap`.
- Respect `prefers-reduced-motion` and `hover: none`, as `global.css` does. Anything shown on hover
  has to be reachable on a touch screen.

## Components: use these before writing new ones

| In Figma's Kit | In code |
|---|---|
| Button | `className="btn"`, `"btn ghost"`, `"btn danger"`; add `small`. Compact is `padding: 6px 12px` |
| Seg option | `<Seg>` in `editor/controls.tsx`. Also `<Row>` for a labelled row and `<Slider>` |
| Toggle | `<Toggle>` in `editor/controls.tsx` |
| Input | `<div className="field"><input /></div>` |
| Status pill | `.project-status` and `StatusDot` in `pages/Projects.tsx` |
| Progress | The upload bar in `pages/Projects.tsx`, or `.meter` |
| Project card | `.project-card` in `pages/Projects.tsx`, laid out by `.project-grid` |
| Empty state | `.empty-state` |
| App header, Site footer | `<AppShell>`, `<SiteFooter>` |

| Drop box | `<DropBox>` in `upload/DropBox.tsx`, with `useUploads` from `upload/store` |
| Usage row | `<UsageRows>` and `<UsageMeter>` in `components/UsageRows.tsx` |
| Workflow step | `<Workflow>` in `agent/Workflow.tsx` |
| Message, Clip suggestion | `<Turn>` in `agent/Turn.tsx` |
| Composer | `<Composer>` in `agent/Composer.tsx` |

Not in the Kit, also available: `.glass` (panel), `.chip`, `.pill`, `.badge`, `.eyebrow`, `.muted`,
`.link-btn`, `.spin`, `.tabs`, `.stat-tile`, `.admin-table`, `.visually-hidden`, `<ClipSettingsDrawer>`,
`useFileDrag()`, `useToast()` from `components/Toast`, and the helpers in `pages/admin/ui.tsx`
(`LoadError`, `ConfirmButton`, `Pager`, `SearchBox`). If one of those helpers is needed outside Admin,
move it rather than copying it.

The main page's upload box takes three of the project grid's four columns and the side cards the fourth,
so their edges line up. Keep that when changing either. Overlays slide over the page: nothing should push
the page down when it opens.

## Icons and images

- There's no icon library, and no image files in the repo. Icons are inline SVG: 24 viewBox,
  `stroke="currentColor"`, stroke width 1.5 to 2, round caps and joins, and `aria-hidden`.
- Video frames come from `/media/{project}/frame.jpg`. Allow for them failing to load: `.project-thumb`
  has its own placeholder.
- Don't import images from Figma unless the design really needs a new asset. Ask first.

## From Figma to code

1. Load the Figma `figma-design-to-code` guidance before calling `get_design_context`. Treat what it
   returns as a reference, not code to paste.
2. Map every part to the table above and every value to a token. A value with no token is a question
   to raise, not a new hardcoded number.
3. Build every state, including those a design may leave out. A data view needs loading, empty and
   error states, and a failed request must never look like an empty list.
4. Don't change behaviour while restyling. `pages/Projects.tsx` starts a tus upload and sets limits
   and messages that the backend relies on.

## Words

- Plain sentences in sentence case, and say what to do next. Never show raw errors to users.
- Keep this wherever a video can be uploaded: "Only upload videos you own or have permission to use."

## Accessibility

Label controls that have no visible text with `aria-label`. Use `role="alert"` for errors and
`role="status"` for progress. Keep the focus style from `--focus`, and keep everything reachable by keyboard.

## Never without asking

Nothing goes to GitHub without the owner's explicit permission for that exact action.
