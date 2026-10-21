# Verdity Design Language

## Overview
**Verdity** — AI-Powered Pull Request Review System with confidence-scored findings, calibrated trust, and full audit integrity.

**Personality:** Precise, Uncompromising, Technical, Bold, Transparent
**Never words:** Soft, Vague, Pastel, Playful, Corporate-blue

**Organizing Idea:** *Binary verdicts in monochrome: black-and-white truth with an orange flare for what matters — every finding earns its color.*

---

## Principles
1. **Monochrome truth, accent conviction** — Black and white carry structure; orange marks only what deserves attention
2. **High contrast as honesty** — No muddy greys pretending to be design; 15:1+ where text lives
3. **Deterministic over magical** — Confidence computed, never self-reported
4. **Audit-first** — Every finding, decision, model call logged with SHA-256 integrity
5. **Calibrated trust** — Weights adjust from human feedback, never per-finding LLM scores
6. **Restraint in accent** — Orange is scarce; scarcity is what makes it loud

---

## Color

### Palette
| Role | Hex | Contrast on White | Contrast on Black | Usage |
|------|-----|-------------------|-------------------|-------|
| **Ink (Primary)** | `#0A0A0A` | 20.4:1 | — | Hero bg, text, code blocks |
| **Paper (Surface)** | `#FFFFFF` | — | 20.4:1 | Page bg, cards |
| **Bone (Alt Surface)** | `#FAFAFA` | — | 19.9:1 | Alternating sections |
| **Ash** | `#666666` | 5.7:1 | 4.0:1 | Secondary text (light bg) |
| **Smoke** | `#A3A3A3` | 2.9:1 | 2.2:1 | Borders, dividers, meta (large text only) |
| **Flare (Accent)** | `#FF5A1F` | 3.1:1 | 6.4:1 | CTAs, active states, key data, accent lines |
| **Flare Deep** | `#D24309` | 4.6:1 | 4.3:1 | Hover on accent, text-on-accent contexts |
| **Signal Green** | `#15803D` | 5.0:1 | 2.6:1 | Success states, passing gates |
| **Signal Red** | `#DC2626` | 4.5:1 | 2.9:1 | Danger, critical findings |

### The Orange Rule
- **Orange appears in ≤10% of any viewport**
- Black text on orange is 6.4:1 (AA) — safe for button labels
- Never for paragraphs, never for borders on cards (black borders only)
- Orange = "look here" — if everything is orange, nothing is

### Contrast Pairs (WCAG 2.2)
- Ink on Paper: 20.4:1 ✅ AAA
- Ink on Bone: 19.9:1 ✅ AAA
- Flare on Ink: 6.4:1 ✅ AA
- Flare Deep on Paper: 4.6:1 ✅ AA
- Ash on Paper: 5.7:1 ✅ AA
- Ink on Flare: 6.4:1 ✅ AA

---

## Typography

### Font Stack
| Role | Font | Fallback | Source |
|------|------|----------|--------|
| **Display / Code** | `JetBrains Mono` | `SF Mono`, monospace | Google Fonts |
| **Heading** | `Space Grotesk` | `Inter`, sans-serif | Google Fonts |
| **Body** | `IBM Plex Sans` | `Inter`, sans-serif | Google Fonts |

### Scale (Major Third 1.250)
| Token | Size | Weight | Usage |
|-------|------|--------|-------|
| `--text-display` | `clamp(3rem, 8vw, 6rem)` | 700 | Hero title |
| `--text-h1` | `clamp(2.25rem, 5vw, 3.5rem)` | 700 | Section titles |
| `--text-h2` | `clamp(1.5rem, 3vw, 2.25rem)` | 600 | Subsections |
| `--text-h3` | `1.25rem` | 600 | Card titles |
| `--text-lg` | `1.125rem` | 400 | Lead paragraphs |
| `--text-base` | `1rem` | 400 | Body |
| `--text-sm` | `0.875rem` | 400 | Metadata |
| `--text-mono` | `0.875rem` | 400 | Code, CLI, tokens |

---

## Spacing & Grid

### Spacing Scale (Base: 4px)
| Token | Value |
|-------|-------|
| `--space-1` | 4px |
| `--space-2` | 8px |
| `--space-3` | 16px |
| `--space-4` | 24px |
| `--space-5` | 32px |
| `--space-6` | 48px |
| `--space-7` | 64px |
| `--space-8` | 96px |
| `--space-9` | 128px |

### Grid
- Max width: `72rem` (1152px)
- 12-column fluid grid
- Section padding: `--space-8` vertical minimum
- Generous whitespace — black-and-white design lives or dies on rhythm

---

## Elevation & Shape

Shadows are nearly banned. Depth comes from **contrast inversion** (black blocks on white), not blur.

| Token | Value | Usage |
|-------|-------|-------|
| `--shadow-1` | `0 1px 2px rgba(10,10,10,0.06)` | Cards (barely there) |
| `--shadow-2` | `0 4px 16px rgba(10,10,10,0.10)` | Hover lift |

| Radius | Value | Usage |
|--------|-------|-------|
| `--radius-sm` | 4px | Inline code |
| `--radius-md` | 8px | Buttons, inputs |
| `--radius-lg` | 16px | Cards, code blocks |
| `--radius-full` | 9999px | Badges, pills |

Borders: `1px solid #0A0A0A` for primary cards — visible structure, not decoration.

---

## Motion

| Token | Value | Usage |
|-------|-------|-------|
| `--duration-fast` | 150ms | Hover, focus |
| `--duration-base` | 250ms | Transitions |
| `--duration-slow` | 450ms | Reveals |
| `--duration-cinematic` | 900ms | Hero sequences |

| Easing | Value | Usage |
|--------|-------|-------|
| `--ease-out` | `cubic-bezier(0.16, 1, 0.3, 1)` | Entrances (expo-out feel) |
| `--ease-in-out` | `cubic-bezier(0.65, 0, 0.35, 1)` | Moves |
| `--ease-snap` | `cubic-bezier(0.9, 0, 0.1, 1)` | Hard cuts, verdict slams |

### Motion Principles
- **Verdict slam**: key numbers/verdicts appear with a fast snap (ease-snap), not a fade
- **Scroll reveals**: one orchestrated stagger per section, not per-element scatter
- **prefers-reduced-motion**: everything instant, no exceptions
- **Transform + opacity only** — compositor tier

---

## Signature Elements

### 1. The Verdict Stamp
Large mono uppercase text with an orange underline bar that snaps in (width 0→100%, ease-snap). Used for hero title and section titles.

### 2. Black Code Terminal
Code blocks are pure black (`#0A0A0A`) with white text and orange syntax accents — the product's native habitat rendered literally.

### 3. Bracket Motif
`< >` brackets in mono, used as section markers. Black on white, orange when active.

### 4. The Orange Tick
Checkbox/checkmark elements in orange — the single touch of color in lists.

---

## Voice & Tone
- **Precise**: "Verdity found 3 high-severity SQL injections" not "issues detected"
- **Verdict-like**: Short declarative sentences. The tool judges; the copy should too.
- **Technical**: Correct terminology, no marketing fog

---

## Accessibility
- WCAG 2.2 AA minimum, AAA for body text
- Focus: 3px `#FF5A1F` outline, 2px offset
- All motion gated behind `prefers-reduced-motion: no-preference`
- Orange never the sole signal — paired with text/shape

---

## Sources
- WCAG 2.2 (W3C) — contrast math
- Swiss/International Typographic Style — monochrome structure with one accent
- Terminal/IDE aesthetics — black surfaces as brand-native texture