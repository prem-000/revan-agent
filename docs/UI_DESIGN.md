# UI_DESIGN.md

Goal: research that is **easy to read** and **easy to understand as a process**. Typography carries the report, and the flow view explains what the agents did.

## 1. Stack

Next.js (React) · Tailwind CSS · shadcn/ui primitives · React Flow (`@xyflow/react`) for the flow view · native `EventSource` for SSE · `next/font` for fonts.

## 2. Screens

1. **Home / Input**: mode toggle (Existing project | New idea), query box with example prompts, folder picker (project mode), depth profile (Quick / Balanced / Deep), source toggles with availability status.
2. **Plan review**: objectives as cards (ID, question, tunnels, expected evidence, criteria). Edit, remove, add, then **Approve**. Shows the project-files-consulted list in project mode.
3. **Live run**: flow diagram + status panel + event log.
4. **Report**: reading view with citations, freshness badges, tabs for the other artifacts.
5. **History**: past runs, approved decisions, project memory controls (view, export, delete).

## 3. Live flow view

A React Flow graph with **one node per graph node**:

```
Analyzer → Planner → [Approval] → Fan-out → (GitHub | Docs | Papers | Web | Community | Local)
        → Normalize → Verify → Critic → ↺ Gap planner → Fan-out   |   → Synthesizer → Report
```

Node states, derived **only** from backend events:

| State | Visual |
|---|---|
| idle | Muted outline |
| running | Accent border + subtle pulse |
| done | Solid fill + check |
| failed | Danger color + message on hover |
| disabled | Dashed outline + reason ("No GITHUB_TOKEN") |
| skipped | Faded |

Additional elements:
- **Loop edge** highlights and shows the round number when the critic sends work back.
- Each tunnel node shows result count, verified count, and latency.
- A budget strip shows LLM calls, retries, round, and elapsed time.
- Clicking a node opens its inputs, outputs, and evidence.
- Respect `prefers-reduced-motion`: no pulse, only color change.

No hard-coded success states. If the backend does not say it, the UI does not show it.

## 4. Report view

- Left: table of contents. Center: article. Right (optional): evidence panel that opens on citation click.
- **Citation chips** `[3]` open the exact excerpt, source link, `published_at`, `updated_at`, and `checked_at`.
- **Freshness badge** next to each source: Fresh · Aging · Stale · Unknown.
- **Evidence state tag**: Retrieved · Verified · Unresolved.
- Visible **"Unresolved questions"** and **"Limitations / verification method"** sections.
- Contradictions shown side by side, never merged.
- Project mode: file citations as `path/to/file.py:42-58`, opening a read-only snippet.
- Tabs: Summary · Findings · Evidence ledger · Comparison · Architecture · Roadmap · Risks · Files consulted.
- Downloads: Markdown and JSON.

## 5. Typography

Pick type for long-form reading first.

| Role | Primary choice | Alternatives |
|---|---|---|
| Report body & headings | **Newsreader** (serif) | Source Serif 4, Literata |
| UI, labels, tables | **Inter** | Geist, IBM Plex Sans |
| Code, paths, versions | **JetBrains Mono** | Geist Mono, IBM Plex Mono |

All are open-source Google Fonts. Load via `next/font` with `display: swap` and a real fallback stack, for example `Newsreader, Georgia, serif`.

### Type scale (rem, base 16px)

| Token | Size / line height | Use |
|---|---|---|
| `display` | 2.25 / 1.15 | Report title |
| `h1` | 1.75 / 1.2 | Section |
| `h2` | 1.375 / 1.3 | Subsection |
| `body` | 1.0625 / 1.7 | Article text (serif) |
| `ui` | 0.9375 / 1.5 | Controls, cards |
| `caption` | 0.8125 / 1.4 | Metadata, badges |
| `code` | 0.875 / 1.6 | Mono |

Rules: article measure **60-72 characters** (`max-w-[68ch]`), generous paragraph spacing, `font-feature-settings` for tabular numbers in tables, real typographic quotes and apostrophes, and no all-caps body text.

## 6. Design tokens

Define colors as CSS variables with light and dark themes (`prefers-color-scheme`, plus a manual toggle).

```css
:root {
  --bg: #fbfaf8;  --surface: #ffffff;  --text: #1d1d1f;  --muted: #6b6b70;
  --border: #e4e2dd;  --accent: #2f4bd8;
  --fresh: #1f7a4d;  --aging: #b07a10;  --stale: #b3402f;  --unknown: #6b6b70;
  --verified: #1f7a4d;  --retrieved: #2f4bd8;  --unresolved: #b3402f;
}
:root[data-theme="dark"] {
  --bg: #121214;  --surface: #1b1b1e;  --text: #ececee;  --muted: #9a9aa2;
  --border: #2a2a2f;  --accent: #7d93ff;
  --fresh: #4cc38a;  --aging: #e0b04a;  --stale: #ef7b6a;  --unknown: #9a9aa2;
  --verified: #4cc38a;  --retrieved: #7d93ff;  --unresolved: #ef7b6a;
}
```

Colors are starting values. Never rely on color alone: every badge also carries a text label and icon.

## 7. Accessibility

- WCAG AA contrast, keyboard navigation for the flow graph and plan editor.
- Meaningful labels for nodes and badges (screen-reader text).
- Reduced-motion support.
- Responsive: report is readable on mobile; flow view collapses to a vertical stage list on narrow screens.

## 8. UI ↔ backend contract

| Action | Endpoint |
|---|---|
| Create run (plan only) | `POST /runs` |
| Get plan | `GET /runs/{id}/plan` |
| Edit / approve plan | `PUT /runs/{id}/plan`, `POST /runs/{id}/approve` |
| Live events | `GET /runs/{id}/events` (SSE, supports `Last-Event-ID`) |
| Get report/artifacts | `GET /runs/{id}/report` |
| Cancel | `POST /runs/{id}/cancel` |
| Project scan (local connector) | `POST /projects/scan` |

Types are generated from the backend's OpenAPI/Pydantic schemas so UI and API cannot drift.
