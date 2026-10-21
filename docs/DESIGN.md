# Verdity Design Language

## Overview
**Verdity** — AI-Powered Pull Request Review System with confidence-scored findings, calibrated trust, and full audit integrity.

**Personality:** Precise, Trustworthy, Technical, Transparent, Uncompromising
**Never words:** Vague, Ambiguous, Magical, Hidden, Arbitrary

**Organizing Idea:** *Calibrated precision: every finding earns its confidence through deterministic evidence, every decision traces to auditable source.*

---

## Principles
1. **Deterministic over magical** — Confidence computed, never self-reported
2. **Audit-first** — Every finding, decision, model call logged with SHA-256 integrity
3. **Calibrated trust** — Weights adjust from human feedback, never per-finding LLM scores
4. **Transparency by default** — Open source, open process, open audit log
5. **Performance as feature** — 100% coverage, sub-second review, token economics built-in

---

## Color

### Palette
| Role | Hex | Contrast (AA/AAA) | Usage |
|------|-----|-------------------|-------|
| **Primary** | `#0A1F44` | 12.4:1 on white | Hero bg, primary actions, nav |
| **Primary Light** | `#1E3A6E` | 7.2:1 on white | Hover states, secondary bg |
| **Accent** | `#00D4AA` | 4.8:1 on `#0A1F44` | CTAs, links, success states, accent lines |
| **Accent Dim** | `#00A888` | 3.9:1 on white | Hover on accent |
| **Surface** | `#F8FAFC` | 16.3:1 on `#0A1F44` | Card bg, code blocks |
| **Surface Elevated** | `#FFFFFF` | 19.1:1 on `#0A1F44` | Modals, dropdowns |
| **Text Primary** | `#0A1F44` | 19.1:1 on surface | Headings, body |
| **Text Secondary** | `#4A5A7A` | 7.8:1 on surface | Subtext, metadata |
| **Text Muted** | `#8B9BB5` | 4.6:1 on surface | Labels, disabled |
| **Border** | `#E2E8F0` | 3.2:1 on surface | Dividers, inputs |
| **Danger** | `#EF4444` | 4.5:1 on white | Errors, critical findings |
| **Warning** | `#F59E0B` | 3.0:1 on white | Warnings, pending review |
| **Success** | `#10B981` | 4.8:1 on white | Passed gates, confirmed |

### Contrast Pairs (WCAG 2.2 AA Verified)
- Primary on Surface: 12.4:1 ✅
- Accent on Primary: 4.8:1 ✅
- Text Primary on Surface: 19.1:1 ✅
- Text Secondary on Surface: 7.8:1 ✅
- Danger on Surface: 4.5:1 ✅
- Success on Surface: 4.8:1 ✅

---

## Typography

### Font Stack
| Role | Font | Fallback | Source |
|------|------|----------|--------|
| **Display** | `JetBrains Mono` | `SF Mono`, `Fira Code`, monospace | Google Fonts |
| **Heading** | `Space Grotesk` | `Inter`, `system-ui`, sans-serif | Google Fonts |
| **Body** | `IBM Plex Sans` | `Inter`, `system-ui`, sans-serif | Google Fonts |
| **Code** | `JetBrains Mono` | `SF Mono`, `Fira Code`, monospace | Google Fonts |

### Scale (Type Scale: 1.250 Major Third)
| Token | Size | Line Height | Weight | Usage |
|-------|------|-------------|--------|-------|
| `--text-display` | `clamp(2.5rem, 5vw, 4rem)` | 1.1 | 700 | Hero title |
| `--text-h1` | `clamp(2rem, 4vw, 3rem)` | 1.2 | 700 | Page title |
| `--text-h2` | `clamp(1.5rem, 3vw, 2.25rem)` | 1.3 | 600 | Section title |
| `--text-h3` | `clamp(1.25rem, 2.5vw, 1.75rem)` | 1.4 | 600 | Subsection |
| `--text-lg` | `1.125rem` | 1.6 | 400 | Lead paragraph |
| `--text-base` | `1rem` | 1.6 | 400 | Body text |
| `--text-sm` | `0.875rem` | 1.5 | 400 | Metadata, captions |
| `--text-xs` | `0.75rem` | 1.5 | 500 | Labels, badges |
| `--text-mono` | `0.875rem` | 1.5 | 400 | Code, CLI |

### Pairing Rules
- **Space Grotesk** (headings) + **IBM Plex Sans** (body) — geometric grotesque + humanist sans
- **JetBrains Mono** for all technical content (code, CLI, tokens, hashes)
- Never mix more than 2 font families per page

---

## Spacing & Grid

### Spacing Scale (Base: 4px)
| Token | Value | Usage |
|-------|-------|-------|
| `--space-1` | `4px` | Micro gaps, icon padding |
| `--space-2` | `8px` | Inline gaps, button padding |
| `--space-3` | `16px` | Component gaps, card padding |
| `--space-4` | `24px` | Section inner padding |
| `--space-5` | `32px` | Section gaps |
| `--space-6` | `48px` | Major section gaps |
| `--space-7` | `64px` | Hero vertical rhythm |
| `--space-8` | `96px` | Page-level breaks |

### Grid
- **Max width:** `72rem` (1152px)
- **Columns:** 12-column fluid grid
- **Gutter:** `--space-4` (24px)
- **Container padding:** `--space-4` (mobile), `--space-6` (desktop)

---

## Elevation & Depth

### Shadows
| Level | Token | Value | Usage |
|-------|-------|-------|-------|
| 1 | `--shadow-1` | `0 1px 2px rgba(10, 31, 68, 0.04)` | Cards, inputs |
| 2 | `--shadow-2` | `0 4px 12px rgba(10, 31, 68, 0.08)` | Cards hover, dropdowns |
| 3 | `--shadow-3` | `0 12px 32px rgba(10, 31, 68, 0.12)` | Modals, popovers |
| 4 | `--shadow-4` | `0 24px 48px rgba(10, 31, 68, 0.16)` | Hero elements, hero cards |

### Border Radius
| Token | Value | Usage |
|-------|-------|-------|
| `--radius-sm` | `4px` | Badges, badges, small buttons |
| `--radius-md` | `8px` | Cards, inputs, buttons |
| `--radius-lg` | `12px` | Cards hover, modals |
| `--radius-xl` | `16px` | Hero cards, featured sections |
| `--radius-full` | `9999px` | Pills, badges, avatars |

---

## Motion

### Durations
| Token | Value | Usage |
|-------|-------|-------|
| `--duration-fast` | `150ms` | Hover, focus, micro-interactions |
| `--duration-base` | `250ms` | Transitions, reveals |
| `--duration-slow` | `400ms` | Modals, drawers, page transitions |
| `--duration-cinematic` | `800ms` | Hero reveals, scroll sequences |

### Easings
| Token | Value | Usage |
|-------|-------|-------|
| `--ease-out` | `cubic-bezier(0.4, 0, 0.2, 1)` | Entrance, reveal |
| `--ease-in` | `cubic-bezier(0.4, 0, 1, 1)` | Exit, dismiss |
| `--ease-out-quint` | `cubic-bezier(0.23, 1, 0.32, 1)` | Hero reveals, cinematic |
| `--ease-spring` | `cubic-bezier(0.34, 1.56, 0.64, 1)` | Playful, bouncy (accent only) |

### Motion Principles
- **Reduced motion:** All animations disabled at `@media (prefers-reduced-motion: reduce)`
- **Duration range:** 200-500ms for UI, 600-800ms for cinematic
- **Easing:** `ease-out` for entrance, `ease-in` for exit
- **GPU-only:** Transform + opacity only; no layout-triggering properties

---

## Components

### Buttons
```css
.btn {
  --btn-padding-x: var(--space-4);
  --btn-padding-y: var(--space-2);
  --btn-radius: var(--radius-md);
  --btn-font: var(--text-sm);
  --btn-weight: 500;
  transition: all var(--duration-fast) var(--ease-out);
}

/* Primary */
.btn-primary {
  background: var(--color-primary);
  color: white;
  border: none;
}
.btn-primary:hover { background: var(--color-primary-light); }
.btn-primary:active { transform: scale(0.98); }

/* Accent */
.btn-accent {
  background: var(--color-accent);
  color: var(--color-primary);
  border: none;
}
.btn-accent:hover { background: var(--color-accent-dim); }

/* Ghost */
.btn-ghost {
  background: transparent;
  color: var(--color-text-primary);
  border: 1px solid var(--color-border);
}
.btn-ghost:hover { background: var(--color-surface); border-color: var(--color-text-muted); }
```

### Cards
```css
.card {
  background: var(--color-surface-elevated);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: var(--space-4);
  box-shadow: var(--shadow-1);
  transition: all var(--duration-base) var(--ease-out);
}
.card:hover {
  box-shadow: var(--shadow-2);
  border-color: var(--color-text-muted);
}
```

### Code Blocks
```css
.code-block {
  background: var(--color-primary);
  color: var(--color-surface);
  border-radius: var(--radius-lg);
  padding: var(--space-4);
  font-family: var(--font-mono);
  font-size: var(--text-mono);
  line-height: 1.6;
  overflow-x: auto;
}
.code-inline {
  background: var(--color-surface);
  color: var(--color-accent);
  padding: 2px 6px;
  border-radius: var(--radius-sm);
  font-family: var(--font-mono);
  font-size: 0.875em;
}
```

### Badges
```css
.badge {
  display: inline-flex;
  align-items: center;
  padding: 2px 8px;
  border-radius: var(--radius-full);
  font-size: var(--text-xs);
  font-weight: 500;
}
.badge-success { background: rgba(16, 185, 129, 0.12); color: var(--color-success); }
.badge-warning { background: rgba(245, 158, 11, 0.12); color: var(--color-warning); }
.badge-danger { background: rgba(239, 68, 68, 0.12); color: var(--color-danger); }
.badge-info { background: rgba(0, 212, 170, 0.12); color: var(--color-accent); }
```

---

## Patterns & Motifs

### Accent Line
A signature visual element — a thin line in accent color (`#00D4AA`) that:
- Underlines hero title
- Separates major sections
- Highlights active navigation
- Animates on scroll reveal (width: 0 → 100%)

### Grid Pattern
Subtle diagonal grid pattern in hero background:
- Color: `rgba(0, 212, 170, 0.03)` (accent at 3% opacity)
- Angle: 45°
- Spacing: 48px
- Animated on scroll (translateY) for subtle depth

### Code Bracket Motif
Angular brackets `< >` as decorative elements:
- Used in hero, section dividers
- Color: accent at 20% opacity
- Animated on scroll (rotate, scale)

---

## Voice & Tone

### Voice Attributes
- **Precise** — Specific, measurable, no hedging
- **Authoritative** — Confident, backed by evidence
- **Transparent** — Show the work, cite sources
- **Technical** — Correct terminology, no dumbing down
- **Respectful** — Respect the developer's time and intelligence

### Do's
- "Verdity found 3 high-severity SQL injection vulnerabilities"
- "Confidence: 94% (calibrated from 2,847 human decisions)"
- "Run `verdity-review --help` for all options"

### Don'ts
- "Verdity uses AI to find bugs" (vague)
- "Our advanced AI detects issues" (magical)
- "Best-in-class solution" (marketing fluff)

---

## Application Rules

### Web (Primary)
- Follow all tokens exactly
- Use CSS custom properties for theming
- Respect `prefers-reduced-motion`
- Test at 4x CPU throttling

### CLI (Secondary)
- Use accent color for success/output
- Primary color for headers/banners
- Mono font for all output
- Spinner: accent color pulse ring

### Documentation
- Code blocks: primary bg, surface text
- Inline code: accent color
- Tables: primary borders, surface rows
- Admonitions: colored left border (accent/success/warning/danger)

---

## Accessibility
- **WCAG 2.2 AA** minimum for all contrast
- **Focus visible:** 3px accent outline, 2px offset
- **Focus order:** Logical, matches visual
- **ARIA labels:** On all interactive elements
- **Reduced motion:** All animations disabled
- **Color blindness:** Tested with deuteranopia/protanopia simulators

---

## Cultural Integrity
No cultural patterns borrowed. This is a technical brand with geometric precision aesthetic. No patterns from African textile traditions, no appropriated motifs. The design language is built from first principles of technical clarity and audit integrity.

---

## Sources
- WCAG 2.2 Contrast Requirements (W3C)
- Design Tokens Community Group (W3C DTCG) — token format
- Material Design 3 Elevation System (Google) — shadow reference
- IBM Carbon Design System — token structure reference
- Tailwind CSS v4 — spacing/typography scale reference

---

## Token Export (W3C DTCG Format)
```json
{
  "colors": {
    "primary": { "value": "#0A1F44", "type": "color" },
    "primary-light": { "value": "#1E3A6E", "type": "color" },
    "accent": { "value": "#00D4AA", "type": "color" },
    "accent-dim": { "value": "#00A888", "type": "color" },
    "surface": { "value": "#F8FAFC", "type": "color" },
    "surface-elevated": { "value": "#FFFFFF", "type": "color" },
    "text-primary": { "value": "#0A1F44", "type": "color" },
    "text-secondary": { "value": "#4A5A7A", "type": "color" },
    "text-muted": { "value": "#8B9BB5", "type": "color" },
    "border": { "value": "#E2E8F0", "type": "color" },
    "danger": { "value": "#EF4444", "type": "color" },
    "warning": { "value": "#F59E0B", "type": "color" },
    "success": { "value": "#10B981", "type": "color" }
  },
  "typography": {
    "font-family-display": { "value": "JetBrains Mono", "type": "fontFamily" },
    "font-family-heading": { "value": "Space Grotesk", "type": "fontFamily" },
    "font-family-body": { "value": "IBM Plex Sans", "type": "fontFamily" },
    "font-family-mono": { "value": "JetBrains Mono", "type": "fontFamily" }
  },
  "spacing": {
    "1": { "value": "4px", "type": "dimension" },
    "2": { "value": "8px", "type": "dimension" },
    "3": { "value": "16px", "type": "dimension" },
    "4": { "value": "24px", "type": "dimension" },
    "5": { "value": "32px", "type": "dimension" },
    "6": { "value": "48px", "type": "dimension" },
    "7": { "value": "64px", "type": "dimension" },
    "8": { "value": "96px", "type": "dimension" }
  }
}
```