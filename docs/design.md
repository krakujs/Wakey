---
version: alpha
name: Wakey-design-system
description: A developer-tools brand for an open-source AI SRE that watches production and wakes up when errors do. Marketing and dashboard surfaces share one "night watch" aesthetic — a blue-black night canvas (#0b0d12) where content floats on cool charcoal cards, and a single scarce accent, Signal Amber (#ffb020), reserved for the moments the system acts. Inter carries display and body in one sans family (400–600); JetBrains Mono carries every log, diff, and terminal surface. The brand's signature is the Night Watch Grid — a 2×2 of live product panes (log stream → fingerprint → ticket → draft PR) backed by an amber spotlight, with the Pulse motif (a flat line that spikes amber when Wakey wakes) marking state everywhere.

colors:
  primary: "#ffb020"
  primary-active: "#e09500"
  primary-glow: "#ffc95c"
  primary-dim: "#8a6210"
  ink: "#ffffff"
  body: "#a3a8b3"
  body-strong: "#ffffff"
  muted: "#7d828d"
  muted-soft: "#565b66"
  hairline: "#23262e"
  hairline-soft: "#191c23"
  hairline-strong: "#333845"
  canvas: "#0b0d12"
  canvas-deep: "#05060a"
  surface-card: "#161920"
  surface-card-elevated: "#1f232c"
  surface-strong: "#282d38"
  on-primary: "#0b0d12"
  on-dark: "#ffffff"
  semantic-error: "#ff5c5c"
  semantic-success: "#2fd67b"
  semantic-info: "#4da6ff"

typography:
  display-mega:
    fontFamily: "'Inter', ui-sans-serif, system-ui, sans-serif"
    fontSize: 72px
    fontWeight: 500
    lineHeight: 1.05
    letterSpacing: -2.5px
  display-xl:
    fontFamily: "'Inter', ui-sans-serif, system-ui, sans-serif"
    fontSize: 56px
    fontWeight: 500
    lineHeight: 1.05
    letterSpacing: -1.9px
  display-lg:
    fontFamily: "'Inter', ui-sans-serif, system-ui, sans-serif"
    fontSize: 44px
    fontWeight: 500
    lineHeight: 1.1
    letterSpacing: -1.5px
  display-md:
    fontFamily: "'Inter', ui-sans-serif, system-ui, sans-serif"
    fontSize: 32px
    fontWeight: 500
    lineHeight: 1.15
    letterSpacing: -1.0px
  display-sm:
    fontFamily: "'Inter', ui-sans-serif, sans-serif"
    fontSize: 24px
    fontWeight: 500
    lineHeight: 1.25
    letterSpacing: -0.5px
  title-md:
    fontFamily: "'Inter', ui-sans-serif, system-ui, sans-serif"
    fontSize: 18px
    fontWeight: 600
    lineHeight: 1.4
    letterSpacing: 0
  title-sm:
    fontFamily: "'Inter', ui-sans-serif, system-ui, sans-serif"
    fontSize: 16px
    fontWeight: 600
    lineHeight: 1.4
    letterSpacing: 0
  body-md:
    fontFamily: "'Inter', ui-sans-serif, system-ui, sans-serif"
    fontSize: 16px
    fontWeight: 400
    lineHeight: 1.55
    letterSpacing: 0
  body-sm:
    fontFamily: "'Inter', ui-sans-serif, system-ui, sans-serif"
    fontSize: 14px
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: 0
  caption:
    fontFamily: "'Inter', ui-sans-serif, system-ui, sans-serif"
    fontSize: 13px
    fontWeight: 400
    lineHeight: 1.4
    letterSpacing: 0
  caption-uppercase:
    fontFamily: "'Inter', ui-sans-serif, system-ui, sans-serif"
    fontSize: 11px
    fontWeight: 600
    lineHeight: 1.4
    letterSpacing: 0.88px
    textTransform: uppercase
  code:
    fontFamily: "'JetBrains Mono', 'Fira Code', monospace"
    fontSize: 13px
    fontWeight: 400
    lineHeight: 1.55
    letterSpacing: 0
  code-sm:
    fontFamily: "'JetBrains Mono', 'Fira Code', monospace"
    fontSize: 12px
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: 0
  button:
    fontFamily: "'Inter', ui-sans-serif, system-ui, sans-serif"
    fontSize: 14px
    fontWeight: 500
    lineHeight: 1.0
    letterSpacing: 0
  nav-link:
    fontFamily: "'Inter', ui-sans-serif, system-ui, sans-serif"
    fontSize: 14px
    fontWeight: 500
    lineHeight: 1.4
    letterSpacing: 0

rounded:
  none: 0px
  xs: 4px
  sm: 6px
  md: 8px
  lg: 12px
  xl: 16px
  pill: 9999px
  full: 9999px

spacing:
  xxs: 4px
  xs: 8px
  sm: 12px
  base: 16px
  md: 20px
  lg: 24px
  xl: 32px
  xxl: 48px
  section: 96px

components:
  top-nav-dark:
    backgroundColor: "{colors.canvas}"
    textColor: "{colors.body-strong}"
    typography: "{typography.nav-link}"
    height: 64px
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.on-primary}"
    typography: "{typography.button}"
    rounded: "{rounded.md}"
    padding: 10px 18px
    height: 40px
  button-primary-active:
    backgroundColor: "{colors.primary-active}"
    textColor: "{colors.on-primary}"
    rounded: "{rounded.md}"
  button-secondary-dark:
    backgroundColor: "{colors.surface-card-elevated}"
    textColor: "{colors.body-strong}"
    typography: "{typography.button}"
    rounded: "{rounded.md}"
    padding: 10px 18px
    height: 40px
  button-outline:
    backgroundColor: transparent
    textColor: "{colors.body-strong}"
    typography: "{typography.button}"
    rounded: "{rounded.md}"
    padding: 9px 17px
    height: 40px
  button-tertiary-text:
    backgroundColor: transparent
    textColor: "{colors.body}"
    typography: "{typography.button}"
  hero-band:
    backgroundColor: "{colors.canvas}"
    textColor: "{colors.body-strong}"
    typography: "{typography.display-mega}"
    padding: 96px
  watch-grid:
    backgroundColor: "{colors.canvas-deep}"
    textColor: "{colors.body-strong}"
    typography: "{typography.code-sm}"
    rounded: "{rounded.xl}"
    padding: 32px
  watch-pane:
    backgroundColor: "{colors.surface-card}"
    textColor: "{colors.body}"
    typography: "{typography.code-sm}"
    rounded: "{rounded.lg}"
    padding: 20px
  pulse-strip:
    backgroundColor: "{colors.canvas}"
    textColor: "{colors.body}"
    rounded: "{rounded.lg}"
  feature-card:
    backgroundColor: "{colors.surface-card}"
    textColor: "{colors.body}"
    typography: "{typography.title-md}"
    rounded: "{rounded.xl}"
    padding: 28px
  connector-card:
    backgroundColor: "{colors.surface-card}"
    textColor: "{colors.body-strong}"
    typography: "{typography.title-sm}"
    rounded: "{rounded.lg}"
    padding: 20px
  connector-icon:
    backgroundColor: "{colors.surface-card-elevated}"
    rounded: "{rounded.md}"
    size: 40px
  status-pill:
    backgroundColor: "{colors.surface-card-elevated}"
    textColor: "{colors.body-strong}"
    typography: "{typography.caption-uppercase}"
    rounded: "{rounded.pill}"
    padding: 4px 10px
  fingerprint-row:
    backgroundColor: "{colors.surface-card}"
    textColor: "{colors.body}"
    typography: "{typography.code-sm}"
    rounded: "{rounded.sm}"
    padding: 10px 14px
  autonomy-dial:
    backgroundColor: "{colors.surface-card-elevated}"
    textColor: "{colors.body}"
    typography: "{typography.caption-uppercase}"
    rounded: "{rounded.pill}"
  confidence-meter:
    backgroundColor: "{colors.surface-card}"
    fillColor: "{colors.primary}"
    textColor: "{colors.body}"
    rounded: "{rounded.pill}"
  cost-chip:
    backgroundColor: "{colors.surface-card-elevated}"
    textColor: "{colors.muted}"
    typography: "{typography.code-sm}"
    rounded: "{rounded.sm}"
    padding: 2px 8px
  diff-block:
    backgroundColor: "{colors.canvas-deep}"
    textColor: "{colors.body}"
    typography: "{typography.code-sm}"
    rounded: "{rounded.lg}"
    padding: 20px
  install-command:
    backgroundColor: "{colors.canvas-deep}"
    textColor: "{colors.body-strong}"
    typography: "{typography.code}"
    rounded: "{rounded.md}"
    padding: 14px 18px
  spotlight-glow-card:
    backgroundColor: "{colors.surface-card}"
    textColor: "{colors.body-strong}"
    typography: "{typography.display-md}"
    rounded: "{rounded.xl}"
    padding: 48px
  badge-pill:
    backgroundColor: "{colors.surface-card-elevated}"
    textColor: "{colors.body-strong}"
    typography: "{typography.caption-uppercase}"
    rounded: "{rounded.pill}"
    padding: 4px 10px
  text-input:
    backgroundColor: "{colors.surface-card}"
    textColor: "{colors.body-strong}"
    typography: "{typography.body-md}"
    rounded: "{rounded.md}"
    padding: 12px 16px
    height: 44px
  cta-band-spotlight:
    backgroundColor: "{colors.canvas}"
    textColor: "{colors.body-strong}"
    typography: "{typography.display-lg}"
    padding: 96px
  footer-dark:
    backgroundColor: "{colors.canvas}"
    textColor: "{colors.body}"
    typography: "{typography.body-sm}"
    padding: 64px 48px

---

## Overview

Where most AI-dev brands sell speed, Wakey sells **vigilance**. The site should feel like a control room at 2am: a blue-black night canvas holding quiet gray type, everything calm — until the one accent color, **Signal Amber**, marks the exact moment the system wakes. Composio's electricity is constant; Wakey's light is **earned**. Amber appears where the product acts: CTAs, the wake moment, live status, the fix. It never appears as decoration.

Type runs **Inter** (open-source stand-in for abcDiatype) as the single sans across display, body, nav, captions — display at weight 500, confident not loud. Every log, diff, terminal, and code surface switches to **JetBrains Mono**. In a product whose entire job is reading machine output, mono isn't a garnish; it's half the brand.

The signature visual is the **Night Watch Grid**: a 2×2 of product-native panes telling the loop's story — a streaming log pane, a fingerprint detection pane, a GitHub ticket pane, a draft-PR pane — with the amber spotlight glow behind them and the Pulse motif running through. Supporting motif: **the Pulse** — a flat hairline waveform that is silent gray at rest and spikes amber at the moment of wake. It appears as the logo mark's heartbeat, section dividers, and status indicators.

**Key characteristics:**
- One accent, earned not painted: `{colors.primary}` only on CTAs, the wake moment, live action, brand glow.
- Dark text on amber CTAs (`{colors.on-primary}`) — a deliberate inversion of white-on-dark norms, for WCAG-passing contrast and a distinctive "lit button" feel.
- Night canvas with cool tint: `{colors.canvas}` (#0b0d12) leans blue-black, versus the neutral grays typical of dark dev sites — it reads as *night*, not just *dark mode*.
- Brightness-step elevation: surfaces step up (`surface-card` → `surface-card-elevated` → `surface-strong`), never drop shadows.
- Product-native imagery: no abstract 3D blobs. Panes show real-looking logs, fingerprints, tickets, diffs — redacted, synthetic, believable.
- The Pulse motif: flat → spike → flat. Used small (status dots), medium (dividers), large (hero backdrop line).
- 96px section rhythm; 1200px cap.

## Colors

### Brand & accent
- **Signal Amber** (`{colors.primary}` — #ffb020): CTAs, wake moments, brand glow center, active states of the autonomy dial. **Scarce by rule.**
- **Signal Amber Active** (`{colors.primary-active}` — #e09500): press state.
- **Wake Glow** (`{colors.primary-glow}` — #ffc95c): radial atmospheric glows only, never fills or text.
- **Amber Dim** (`{colors.primary-dim}` — #8a6210): amber's quiet form — borders and icons where full amber would shout (e.g. a paused watcher).
- **On-primary** (`{colors.on-primary}` — #0b0d12): near-black text/icons on amber. White on amber fails contrast (~1.8:1); dark-on-amber passes at ~10:1. This inversion is non-negotiable.

### Surface (night ladder)
- **Canvas** (`{colors.canvas}` — #0b0d12): page floor. Blue-tinted night, not neutral black.
- **Canvas Deep** (`{colors.canvas-deep}` — #05060a): recessed surfaces only — watch grid, code, diffs, terminals.
- **Surface Card** (`{colors.surface-card}` — #161920): default card.
- **Surface Card Elevated** (`{colors.surface-card-elevated}` — #1f232c): inner panes, secondary buttons, inputs' resting state.
- **Surface Strong** (`{colors.surface-strong}` — #282d38): menus, popovers, active rows.

### Hairlines
`{colors.hairline}` #23262e default · `{colors.hairline-soft}` #191c23 light · `{colors.hairline-strong}` #333845 emphasized panels.

### Text
Ink #ffffff display · Body `{colors.body}` #a3a8b3 running text · Muted #7d828d subtitles · Muted-soft #565b66 disabled.

### Semantic — reserved, never decorative
- **Error** (`{colors.semantic-error}` — #ff5c5c): only for actual errors/severity in product data (sev pills, diff minus context is *not* semantic).
- **Success** (`{colors.semantic-success}` — #2fd67b): verified fixes, healthy services, passing tests.
- **Info** (`{colors.semantic-info}` — #4da6ff): neutral system notices (P2+ use).
- **Rule:** amber is *never* used to mean "warning" — that would burn the brand accent into semantics. Warnings use semantic tokens if needed.

## Typography

Single sans: **Inter** across every role (self-hosted via @font-face; no CDN dependency — this is a self-hosted product, its site shouldn't phone home either). Code: **JetBrains Mono**, self-hosted likewise. Fallbacks: `ui-sans-serif, system-ui, sans-serif` / `monospace`.

| Token | Size | Weight | LH | Tracking | Use |
|---|---|---|---|---|---|
| `{typography.display-mega}` | 72px | 500 | 1.05 | -2.5px | Hero h1 |
| `{typography.display-xl}` | 56px | 500 | 1.05 | -1.9px | Secondary heroes |
| `{typography.display-lg}` | 44px | 500 | 1.1 | -1.5px | Section heads |
| `{typography.display-md}` | 32px | 500 | 1.15 | -1.0px | Sub-sections, spotlight cards |
| `{typography.display-sm}` | 24px | 500 | 1.25 | -0.5px | Card group titles |
| `{typography.title-md}` | 18px | 600 | 1.4 | 0 | Component titles |
| `{typography.title-sm}` | 16px | 600 | 1.4 | 0 | Card titles |
| `{typography.body-md}` | 16px | 400 | 1.55 | 0 | Body |
| `{typography.body-sm}` | 14px | 400 | 1.5 | 0 | Dense body, footer |
| `{typography.caption}` | 13px | 400 | 1.4 | 0 | Captions |
| `{typography.caption-uppercase}` | 11px | 600 | 1.4 | 0.88px | Labels, badges, status pills |
| `{typography.code}` | 13px | 400 | 1.55 | 0 | Commands, install blocks |
| `{typography.code-sm}` | 12px | 400 | 1.5 | 0 | Log/diff/terminal panes |
| `{typography.button}` | 14px | 500 | 1.0 | 0 | CTAs |
| `{typography.nav-link}` | 14px | 500 | 1.4 | 0 | Nav |

**Principles:** display stays at 500 (never 400, never 700+); one sans family, no display/body split; mono owns every machine-output surface; uppercase 11px labels are the "instrument panel" voice — use them to name states (`WATCHING`, `WAKING`, `VERIFYING`), not to shout marketing.

## Layout

- Base unit 4px; tokens xxs 4 → section 96px. Major bands at 96px; cards inside a band at 24px.
- Max width 1200px; 12-column editorial grid.
- Night Watch Grid: 2×2 equal panes, 16px gap, inside a 32px-padded canvas-deep container.
- Connector grid: 4-up desktop / 2-up tablet / 1-up mobile.
- Footer: 5-column desktop.

**Whitespace philosophy:** the night canvas carries depth, so spacing stays tight without crowding — but every hero gets air: the quiet before the spike is part of the story.

## Elevation & depth

| Level | Treatment | Use |
|---|---|---|
| Flat (canvas) | `{colors.canvas}` | Bands, footer |
| Recessed | `{colors.canvas-deep}` | Watch grid, code, diffs |
| Card | `{colors.surface-card}` | Default cards |
| Card elevated | `{colors.surface-card-elevated}` | Panes, secondary buttons |
| Strong | `{colors.surface-strong}` | Menus, active rows |
| Atmospheric | Radial glow, `{colors.primary-glow}` → transparent | Behind hero/watch-grid and CTA band only |

No drop shadows, ever. Depth = brightness steps + at most one radial amber glow per viewport.

## Shapes

Radii: xs 4px inline tags · sm 6px rows · **md 8px all CTAs and inputs (never full pills)** · lg 12px cards, panes, code · xl 16px large cards and the watch grid · pill only for badges/status/autonomy dial. Developer-ergonomic geometry, same dialect as Composio — this is table stakes for the category.

## Components

### Navigation
**`top-nav-dark`** — canvas bg, 64px, body-strong links. Left: Wakey wordmark + Pulse mark (a dot that "breathes" — see Motion). Center/right: How it works / Connectors / Self-host / Docs; right: GitHub stars, Sign in, **Get started** (button-primary).

### Buttons
- **`button-primary`** — amber fill, **near-black text** (`on-primary`), 8px radius, 40px. Hover: brightness +6%; active: `{colors.primary-active}`. This is the only filled-accent element on a page.
- **`button-secondary-dark`** — elevated surface, white text. For "Self-host install", secondary paths.
- **`button-outline`**, **`button-tertiary-text`** — hairline-strong border / text link, as standard.

### Hero & signature
- **`hero-band`** — canvas bg, display-mega h1, subhead (body-md, body color), primary + secondary CTA, amber radial glow low behind the watch grid. Hero copy voice: calm, declarative, night-watch. ("Your services are being watched. On purpose.")
- **`watch-grid`** — **the signature.** 2×2 panes in a canvas-deep container. Each `watch-pane` is a product moment: (1) **log stream** — scrolling mono lines, one line trips into amber; (2) **fingerprint** — "fp:3f9a ×217 in 12m" row with severity pill and "since deploy a1b2c3d"; (3) **ticket** — a GitHub-style issue card with RCA headline and confidence; (4) **draft PR** — mini diff (green/red semantic lines) + "awaiting review" status. A thin amber Pulse line runs along the grid's left edge, spiking at pane transitions. Panes are stylized but *technically believable* — no lorem, real-shaped stack traces, redaction markers (`[REDACTED:aws_key]`) visible as a feature.
- **`pulse-strip`** — the loop as a horizontal line: quiet → detect → ticket → RCA → PR → verified, with the amber spike traveling to the active stage. Used in "How it works" and as an auto-demo narrative device.
- **`spotlight-glow-card`** — large statement card with centered glow, for trust/positioning statements.

### Cards
- **`feature-card`** — 3-up benefits; icon in elevated plate; title-md; one paragraph.
- **`connector-card` + `connector-icon`** — 4-up grid (GCP, AWS, Azure, Docker, Sentry, Loki, Datadog, webhook…). Mono caption under each: one-line "logs in via X". Icons monochrome gray; the one in use may hold amber-dim.

### Product-native components (Wakey-specific — no Composio equivalent)
- **`status-pill`** — the state voice: `WATCHING` (gray), `WAKING` (amber), `INVESTIGATING` (amber), `VERIFYING` (info blue), `VERIFIED` (semantic green), `BLOCKED` (semantic red). Always caption-uppercase, always with a status dot.
- **`fingerprint-row`** — table row: mono fingerprint hash, message template, ×count, sev pill, state pill, age. Hover lifts to surface-strong.
- **`autonomy-dial`** — segmented pill: `OBSERVE / TRIAGE / DIGEST / FIX`. Active segment amber with dark text. The product's most important control gets its own component — it *is* the trust story.
- **`confidence-meter`** — slim horizontal bar, amber fill on elevated track, mono % label; caption beneath: calibration note ("this band is right ~7 times in 10").
- **`cost-chip`** — tiny mono chip: `$0.42 · 4 runs`. Cost transparency as a visual element.
- **`diff-block`** — canvas-deep, mono-sm; `+` lines semantic-success-tinted background, `-` semantic-error-tinted; file header row with hairline; line numbers muted-soft.
- **`install-command`** — the self-host moment: full-width mono block `git clone … && docker compose up -d`, copy affordance, amber left border. This block is the "open-source" brand moment — treat it like a hero.

### Forms
**`text-input`** — surface-card, 44px, 8px radius; focus: 1px amber border + glow-dim ring. Validation errors: semantic-error text, never amber.

### Interaction guardrails (binding — derived from ui-ux-pro-max audit)

- **Focus visibility**: every interactive element (buttons, inputs, cards-as-links, autonomy dial segments, copy buttons) gets a visible focus state: `2px {colors.primary}` ring with 2px offset, plus `glow-dim` on dark surfaces. Keyboard nav order must match visual order.
- **Touch targets**: 44×44px minimum hit area on all interactive elements (WCAG AAA / mobile). CTAs render at 40px visual height but extend their hit area to 44px via padding/pseudo-element; inputs are 44px. Never ship a sub-44px tap target on touch layouts.
- **Cursor & hover**: `cursor-pointer` on every clickable; hover feedback = color/brightness/border transitions only, 150–300ms — **no scale transforms** (they shift layout), no instant state jumps.
- **Loading states**: buttons disable during async ops (spinner inside, label retained); async panes reserve their space and show skeleton (`animate-pulse` on surface-card blocks) — never blank, never layout shift (no content jumping).
- **z-index scale** (tokens, never arbitrary values): `z-10` dropdowns · `z-20` sticky nav · `z-30` popovers/tooltips · `z-40` modals · `z-50` toasts. Stacking contexts are understood, not fought with `z-[9999]`.
- **Icons**: SVG only — Lucide or Heroicons for UI glyphs (24px viewBox, consistent sizing), Simple Icons for connector/brand logos (verified paths). **Never emoji as UI icons.**
- **Contrast floor 4.5:1 for all text**: verified pairs — `body` on canvas ≈7.6:1 ✓, `muted` on canvas ≈5:1 ✓, `muted-soft` ≈2.6:1 ✗ → **muted-soft is restricted to decorative/disabled use, never readable text**. Status pills always pair color with a text label (color is never the only indicator).
- **Prose measure**: marketing paragraphs cap at 65–75ch; minimum 16px body on mobile — `caption`/`code-sm` sizes are for labels and machine output only, never reading text.

### CTA / footer
- **`cta-band-spotlight`** — pre-footer: display-lg + single primary CTA over a low amber glow. Copy voice: the quiet promise ("Sleep. Wakey won't.").
- **`footer-dark`** — 5 columns (Product / Connectors / Self-host / Community / Company), body-sm; GitHub + Discord/Community links; "Apache-2.0 · self-hosted · your logs stay yours" line.

## Motion (Wakey addition — Composio left this open)

- **Micro-interactions** (hover, copy feedback, dial switching): 150–300ms ease-out; transform/opacity only (never width/height); color/brightness feedback, never scale.
- **Breathing status dot**: 2.4s ease-in-out infinite, opacity 0.5→1. Infinite animation is reserved exclusively for status/loading indicators (this is one) — decorative elements never loop.
- **The wake moment**: pulse spike + amber fill animate **once**, 300ms ease-out. Never looping.
- **Pulse-strip traveler**: the amber marker moves stage-to-stage in ~400ms — a narrative animation, not a micro-interaction; plays once per viewport entry.
- **Glow**: static. Glows never pulse or rotate.
- **`prefers-reduced-motion`**: all of the above collapse to static states; breathing dots become static pills; skeletons remain (they're informational, and pause pulsing). Mandatory, not optional.

## Do's and don'ts

### Do
- Reserve amber for action: CTAs, the wake moment, live status, the active autonomy segment.
- Use near-black (`on-primary`) text on every amber surface — never white.
- Keep the night canvas blue-tinted (`#0b0d12`), and canvas-deep reserved for machine output (logs/code/diffs).
- Show real-shaped product artifacts: stack traces, fingerprint hashes, confidence meters, redaction markers.
- Use the uppercase status-pill vocabulary (`WATCHING`, `WAKING`, `VERIFYING`) as the interface's narrative voice.
- Pair every hero with at most one radial amber glow — calm elsewhere.
- Animate the wake, once. Then be still.

### Don't
- Don't use amber to mean "warning" or decorate non-action elements — scarcity is the brand.
- Don't use white text on amber, or full-pill CTAs.
- Don't add drop shadows, glassmorphism, gradient meshes, or a second brand hue.
- Don't use semantic red/green as decoration — they're reserved for product truth.
- Don't show fake-generic code (`lorem`, `foo.bar`) in panes — every pane must read like real Wakey output.
- Don't animate glows, and never loop the wake moment.
- Don't load fonts or assets from third-party CDNs — the site must work air-gapped, like the product.

## Responsive behavior

| Breakpoint | Changes |
|---|---|
| Mobile < 640px | Hero 72→36px; watch-grid collapses to a single-pane vertical story (log → fingerprint → ticket → PR, scroll-narrated); connector grid 1-up; nav hamburger; install-command wraps with copy icon. |
| Tablet 640–1024px | Hero 56px; watch-grid stays 2×2; connectors 2-up. |
| Desktop 1024–1280px | Full hero, 2×2 grid, connectors 4-up. |
| Wide > 1280px | Content caps 1200px. |

Touch targets: **44×44px hit areas** everywhere (CTAs render at 40px visual height with extended hit area; inputs 44px). Glow persists at every breakpoint — it's the brand's night light.

## Site blueprint (marketing site v1)

1. **Nav** — wordmark + pulse dot, links, GitHub stars, Get started. Sticky, with the CTA visible at all scroll depths (landing best practice: sticky hero-placed CTA + deep CTA after the trust band).
2. **Hero** — badge "open-source · self-hosted · no SDK"; H1: the night-watch promise; sub: the loop in one sentence; CTAs `Get started` / `View on GitHub`; **Night Watch Grid** with glow; below it, the pulse-strip captioned "log → ticket → RCA → fix → verified".
3. **Proof strip** — connector cards (logs in from GCP, AWS, Azure, Docker, anything-with-a-webhook) + the no-SDK claim.
4. **How it works** — 4 feature-cards: wakes up on real errors (fingerprint/dedup), correlates with your deploys, investigates like an engineer (RCA + confidence), proposes repro-first fixes. Each card shows a mini product artifact, not an illustration.
5. **Trust band** (`spotlight-glow-card`) — "It suggests. You merge." + the autonomy-dial rendered live + redaction/local-LLM notes.
6. **Open-source band** — `install-command` block, Apache-2.0, stars, CONTRIBUTING link, eval-bench honesty ("published fix-quality numbers, not vibes").
7. **CTA band** — display-lg + primary CTA.
8. **Footer** — 5 columns + the "your logs stay yours" line.

## Dashboard application (same tokens, quieter)

The self-hosted dashboard (WF-01 wizard, OPS-1) reuses this system with marketing elements stripped: no glows except the service-status pulse; sidebar nav on canvas; services as feature-cards; fingerprint table as `fingerprint-row` list; ticket/PR mirrors as cards with `status-pill`, `confidence-meter`, `cost-chip`; the setup wizard is a full-screen surface-card sheet with the amber progress thread. Everything readable at 2am — this UI's real users are literally on call at night.

**Page inventory & behaviors** are specced in [`docs/workflows/WF-12-dashboard-gui.md`](workflows/WF-12-dashboard-gui.md): the server-start banner + auto-open + one-time first-run token, the eight pages, cross-cutting rules (read-only GitHub mirrors, guardrail confirms, empty states, 375px on-call usability).

**CLI & terminal voice (`wakey` — WF-13):** the brand extends to the terminal. The `wakey` CLI uses the same status-pill vocabulary as plain text (`WATCHING`, `WAKING`, `VERIFYING`), amber in color mode only for attention/action states, and `--no-color`/non-TTY output must lose zero information (status is always words, color is reinforcement). The **start banner is the brand's handshake**: minimal mono block — wordmark glyph, version, dashboard URL, one-time setup token, `wakey status` hint — printed when the server comes up. JetBrains Mono everywhere; ASCII tables aligned like an instrument panel, not a spreadsheet dump.

## Iteration guide

1. One component per change; variants live inside `components:`.
2. `{token.refs}` everywhere — never inline hex.
3. Amber passes the "would a lighthouse stand here?" test: if it's not where attention must land, it's gray.
4. Every new component needs a dark-state, a focus-state, and a reduced-motion story before merge.
5. Copy check: if a headline would fit any AI startup, rewrite it until only Wakey can say it.

### Working with the ui-ux-pro-max skill (for agents implementing UI)

`docs/design.md` is the source of truth for brand identity; the `ui-ux-pro-max` skill (at `~/.agents/skills/ui-ux-pro-max`) is the implementation-intelligence layer. Per page/build task:

1. Run `search.py "<page keywords>" --design-system` for a fresh recommendation — **treat its palette/style as advisory only**; our brand tokens here always win (its generic slate+green palette has already been considered and rejected).
2. Use domain searches for the craft: `--domain ux` (animation, loading, z-index, a11y), `--domain landing` (page structure), and `--stack <stack>` for implementation idioms once the stack is chosen.
3. The **pre-delivery checklist** below gates every UI PR — it's also binding under `docs/engineering-standards.md` §1.

### Pre-delivery checklist (gates every UI PR)

- [ ] No emojis as icons — Lucide/Heroicons SVG set, consistent 24px viewBox; brand logos verified via Simple Icons
- [ ] `cursor-pointer` on every clickable element
- [ ] Hover/focus states: color/brightness transitions 150–300ms, no layout shift, visible amber focus ring
- [ ] Text contrast ≥4.5:1 (muted-soft never used as readable text); color never the sole indicator
- [ ] Touch hit areas ≥44×44px; responsive checked at 375 / 768 / 1024 / 1440px; no horizontal scroll
- [ ] Async content: skeletons + reserved space; buttons disable while pending
- [ ] z-index from the token scale only
- [ ] `prefers-reduced-motion` respected (wake moment and breathing dots collapse to static)
- [ ] No third-party CDN requests (fonts/assets self-hosted)

## Known gaps

- Inter is the substitute; if the project later licenses a distinctive face, only display weights should change — body stays Inter.
- Exact glow opacity/blur values need tuning against real renders (this spec fixes structure and scarcity, not blur radii).
- Dashboard data-density modes (compact tables for big estates) unspecified until OPS-1 design pass.
- Dark-only. A light theme is out of scope by philosophy (the night watch doesn't have a day shift) — revisit only if accessibility data demands it.
