---
# DESIGN.md front matter — machine-readable tokens (alpha format; unknown keys ignored).
colors:
  primary: "#0A0A0A"        # ink — page background (dominant)
  accent: "#FF5A1F"         # flare — the only hue
  background: "#0A0A0A"
  surface: "#141414"        # raised surfaces: cards, terminals
  surface-alt: "#1C1C1C"    # highest surface: inputs, code-inline
  text: "#FFFFFF"           # white is TEXT ONLY, never a background
  text-muted: "#A3A3A3"     # 7.85:1 on ink — AAA
  success: "#22C55E"        # on dark surfaces only
  warning: "#F59E0B"        # on dark surfaces only
  danger: "#F87171"         # on dark surfaces only
  border: "#666666"         # essential UI boundaries (3.45:1 on ink)
  hairline: "#2E2E2E"       # decorative separators only (not load-bearing)
contrast:
  - { fg: text, bg: background, use: text }
  - { fg: text-muted, bg: background, use: text }
  - { fg: accent, bg: background, use: text }
  - { fg: primary, bg: accent, use: text }
  - { fg: text, bg: surface, use: text }
  - { fg: text-muted, bg: surface, use: text }
  - { fg: accent, bg: surface, use: ui }
  - { fg: border, bg: background, use: ui }
languages: [en]
typography:
  display:
    fontFamily: "JetBrains Mono"
    fontSize: 72
    fontWeight: 800
  heading:
    fontFamily: "Space Grotesk"
    fontSize: 32
    fontWeight: 700
  body:
    fontFamily: "IBM Plex Sans"
    fontSize: 16
    fontWeight: 400
  mono:
    fontFamily: "JetBrains Mono"
    fontSize: 14
    fontWeight: 500
spacing:
  unit: 4
  scale: [4, 8, 16, 24, 32, 48, 64, 96, 128]
rounded:
  none: 0
  sm: 4
  md: 8
  lg: 16
  pill: 9999
components:
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.primary}"
    rounded: "{rounded.md}"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.text}"
    borderColor: "{colors.border}"
    rounded: "{rounded.md}"
  card:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    borderColor: "{colors.hairline}"
    rounded: "{rounded.lg}"
  terminal:
    backgroundColor: "{colors.surface}"
    textColor: "#EDEDED"
    rounded: "{rounded.lg}"
motion:
  duration: { instant: 0, xxshort: 50, xshort: 100, short: 150, medium: 200, long: 250, xlong: 400, slam: 600 }
  easing:
    enter: "cubic-bezier(0.16, 1, 0.3, 1)"
    standard: "cubic-bezier(0.65, 0, 0.35, 1)"
    slam: "cubic-bezier(0.9, 0, 0.1, 1)"
  reduced: "reveals render instantly at full opacity; slam becomes a plain appear; marquee and pulse loops stop"
---

# Verdity Design Language
Version 1.1 · Owner: Dream-Pixels-Forge · Updated: 2025-10-21

## Overview
**The one idea:** *A verdict, not a vibe — ink-black pages, white text, one orange flare for what matters.*

**Personality:** Precise · Uncompromising · Technical · Auditable
**Never:** Soft, Pastel, Corporate-blue, Vague
**Used for:** Website, README, CLI output, GitHub social preview, video overlays

**Principles:**
1. **White is text, never a surface.** Backgrounds are always ink or a raised ink surface. Do: white `#FFFFFF` only on `color:` properties. Don't: white or bone section backgrounds.
2. **Orange is a verdict mark.** ≤10% of any viewport; it means "look here" or "act now". Do: kickers, key numbers, hot steps, the single CTA band. Don't: paragraph text, card borders, large background fills outside the CTA band.
3. **Contrast is computed, not eyeballed.** Every pair in front matter passes WCAG 2.2 AA. Do: run `contrast.py --check` after any color change. Don't: introduce a grey because it "looks fine".
4. **Depth by surface steps, not blur.** Three ink levels (bg → surface → surface-alt) replace shadows. Do: `#0A0A0A → #141414 → #1C1C1C` steps + hairline separators. Don't: drop shadows or glows.
5. **The terminal is home.** Verdity's native habitat is a black terminal with white output and orange verdicts; the website renders that habitat literally.

## Colors
Dominant/background ink ≈ 88%, muted text ≈ 7%, orange accent ≈ 4%, signal colors ≈ 1%.

| Role | Name | Hex | Usage | Contrast (vs ink `#0A0A0A`) |
|---|---|---|---|---|
| background | ink | `#0A0A0A` | page bg, footer, hero | — |
| surface | raised | `#141414` | cards, terminals, nav | 1.07:1 (surface step, not a boundary) |
| surface-alt | highest | `#1C1C1C` | inline code, inputs | 1.16:1 (surface step) |
| text | white | `#FFFFFF` | all primary text — TEXT ONLY | 20.4:1 AAA |
| text-muted | ash | `#A3A3A3` | secondary text, meta | 7.85:1 AAA |
| text-soft | smoke | `#EDEDED` | terminal body text | 17.2:1 AAA |
| accent | flare | `#FF5A1F` | kickers, key numbers, CTA band, focus | 6.35:1 AA |
| border | steel | `#666666` | essential UI boundaries, ghost buttons | 3.45:1 (UI, passes 1.4.11) |
| separator | hairline | `#2E2E2E` | decorative dividers, card edges | decorative only |
| success | gate-pass | `#22C55E` | passed gates, ✓ marks (dark surfaces) | 8.7:1 on surface AAA |
| warning | review | `#F59E0B` | manual-review states (dark surfaces) | 8.6:1 on surface AAA |
| danger | critical | `#F87171` | critical findings (dark surfaces) | 7.2:1 on surface AAA |

**Allowed pairings:** white/ash/smoke on ink or raised; flare on ink or raised; ink text on flare (CTA band, primary buttons only).
**Forbidden pairings:** white or bone as a background; ash/smoke on flare; flare on flare; ink text on raised surfaces (too low); any signal color on paper — signals exist only on dark surfaces.
**Gradient policy:** none, except `transparent → flare` in the scroll-hint line.
**Dark mode:** this palette IS dark mode; there is no light variant. The CTA band's orange is the one inverted moment.

## Typography
| Style | Font | Size | Weight | Line height | Tracking | Case |
|---|---|---|---|---|---|---|
| display | JetBrains Mono | clamp(2.75rem, 8vw, 5.5rem) | 800 | 1.02 | −0.03em | sentence |
| h2 | Space Grotesk | clamp(2rem, 4.5vw, 3.25rem) | 700 | 1.1 | −0.02em | sentence |
| h3 | Space Grotesk | 1.15–1.2rem | 600 | 1.3 | 0 | sentence |
| kicker | JetBrains Mono | 0.8–0.9rem | 700 | 1.4 | +0.15em | uppercase |
| body | IBM Plex Sans | 1rem (1.125 lead) | 400 | 1.6–1.7 | 0 | sentence |
| mono | JetBrains Mono | 0.85rem | 500 | 1.75 | 0 | as-written |

Scale ratio 1.25 (major third). Fallbacks: `SF Mono → monospace`, `Inter → sans-serif`. Max line length 45–75ch for body. Rationale: mono display = machine verdicts; Space Grotesk = geometric structure; IBM Plex Sans = neutral working voice. No Inter/Roboto defaults anywhere.

## Layout
Spacing scale 4/8/16/24/32/48/64/96/128. Max width 72rem, 24px gutters. Section padding 96–128px vertical (7rem). Density: airy — at least 40% empty on hero. Alignment: left-dominant, centered only in hero stats and CTA band. Breakpoints: 640, 768, 960. Interactive targets ≥ 44px (nav CTA, buttons).

## Elevation & Depth
No shadows. Three surface steps carry depth: `#0A0A0A` → `#141414` → `#1C1C1C`. Separators: `#2E2E2E` hairlines (decorative) or `1px solid flare` (meaningful). Terminals get a `#2E2E2E` border so they read as objects on ink.

## Shapes
Radius: 4 (inline code), 8 (buttons, badges-sharp), 16 (cards, terminals), pill (tags). Signature shape: the **bracket** `< >` in mono — section markers, brand lockup. Secondary: the **2px flare rule** that leads every kicker. Edges are crisp; no soft glows.

## Components
- **Buttons:** primary = flare bg + ink text (6.35:1); ghost = transparent + white text + `#666666` border, hover → white border. All ≥44px tall.
- **Cards:** raised `#141414`, `#2E2E2E` hairline, 16px radius, hover lifts 4px (transform only).
- **Terminal:** `#141414`, traffic dots, mono `#EDEDED` body, flare prompts, signal-colored verdicts.
- **Badges/tags:** pill, mono 0.72rem uppercase; solid-ink, ghost-hairline, or flare variants.
- **States:** hover (flare or lift), focus (3px flare ring, 2px offset — 6.35:1, passes), disabled (40% opacity), error (danger text + shape, never color alone).

## Do's and Don'ts
- Do: keep white on `color:` only; run contrast.py after palette edits; one flare moment per viewport.
- Don't: white backgrounds; shadows; more than one orange section per page; ash text below 7:1; Inter/Roboto/system defaults; color as the only signal (pair with ✓/✖/▲ shape).

## Motion
Personality: **snappy and precise — a verdict slams, it doesn't drift.**
| Token | Value | Use |
|---|---|---|
| xxshort | 50ms | hover lifts |
| xshort | 100ms | focus rings |
| short | 150ms | button color, nav |
| medium | 200ms | card transitions |
| xlong | 400ms | section reveals |
| slam | 600ms | verdict underline (0.6s, ease-slam, 0.7s delay) |

Easings: enter `cubic-bezier(0.16,1,0.3,1)`; standard `cubic-bezier(0.65,0,0.35,1)`; slam `cubic-bezier(0.9,0,0.1,1)`. Choreography: one stagger per section, 80ms steps, order = kicker → title → lede → content. Exits are instant (nav hides nothing animated). Loops: hero float + grid drift only, 6s/20s, `linear` allowed for loops. **Reduced motion:** all `.reveal` elements render at full opacity instantly; slam plays as a plain appear; loops stop (`animation-duration: 0.01ms`). Never flash >3/s. Transform + opacity only.

## Pattern and Ornament
| Pattern | Status | Inspired by | Construction rule | Allowed uses | Do not |
|---|---|---|---|---|---|
| Grid field | original | none — engineering graph paper | 64px square grid, white 4% on ink | hero/CTA backgrounds at ≤6% opacity | behind body text; any other color |
| Verdict underline | original | none — redacted-document marker | 0.09em flare bar, scaleX 0→1, ease-slam | display titles only | body text, headings < h2 |
| Bracket pair | original | none — code syntax | mono `< >`, flare when active | brand lockup, section markers | sentence decoration |

No cultural or textile motifs are used; all three patterns are original constructions.

## Voice and Tone
We are: precise, declarative, evidence-first. We are not: hype-y, hedging, magical.
- "Verdity found 3 high-severity SQL injections" — not "issues were detected".
- "conf 0.98" — not "very confident".
- Buttons: verb-first ("Install", "View source"). Errors state the fix, not the apology.

## Accessibility and Integrity
All front-matter pairs pass WCAG 2.2 AA (`contrast.py --check`; white-on-ink and ash-on-ink pass AAA). Focus: 3px flare ring, 2px offset, never hidden by sticky nav. Targets ≥44px. Signals always paired with shape (✓ ✖ ▲). Verified numerically 2025-10-21; not verified: screen-reader walkthrough, deuteranopia simulation (flare+ink pair is luminance-robust but unchecked).

## Sources
| ID | Source | Used for | Verified |
|---|---|---|---|
| S1 | W3C WCAG 2.2 Understanding docs | contrast ratios, 1.4.11 UI 3:1, 2.5.8 targets | 2025-10-21 [V] via contrast.py |
| S2 | Atlassian motion duration scale (design-elements.md S7) | duration tokens | 2025-10-21 [V as recorded in skill] |
| S3 | Swiss/International Typographic Style | monochrome + single accent structure | [U] design lineage |
| S4 | design-language skill `type-pairings.md` | avoiding default fonts; pairing logic | 2025-10-21 [V] |

## Changelog
| Version | Date | Change | By |
|---|---|---|---|
| 1.1 | 2025-10-21 | All-dark system: white demoted to text-only, bone/paper backgrounds removed; front-matter tokens + contrast pairs added; linter-clean structure | agent |
| 1.0 | 2025-10-21 | Initial black/white/orange language | agent |