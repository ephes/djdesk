# 2025-11-16 — DJDesk Tutorial App Content & Docs Scope

## 1. Objective

- Ship a tutorial-grade Electron + Django desktop experience that moves beyond “hello world” and demonstrates why a local desktop wrapper around Django is valuable.
- Bundle the tutorial narrative with documentation that mirrors the `django-indieweb` style (Sphinx + Read the Docs) so contributors have a consistent learning experience across repos.
- Show at least three capabilities that are either painful or impossible in a pure web deployment (offline state, deep OS integration, system automation) to justify Electron.

## 2. Inputs & Research Notes

| Source | Takeaways |
| --- | --- |
| `README.md` & current repo | Only the default Django startproject is exposed; no frontend build toolchain or docs exist yet, so the spec must define both the narrative and the supporting assets. |
| `specs/2025-11-15_django_electron.md` | Packaging strategy is already being worked out, so this spec can assume the Electron shell is viable and focus on *what* the tutorial showcases. |
| `../django-indieweb/docs/*.rst` | Documentation uses Sphinx with `.rst` pages, badges, and Read the Docs publishing—match this tone/structure for DJDesk. |
| https://www.bayesian.energy/convexity | The page is a Next.js site (note the `_next` assets) rather than an Electron app, but it demonstrates a slick, editorial layout with cinematic typography, muted gradients, and high-contrast cards. Use it as a visual benchmark. |

## 3. Product Vision & Narrative

**Concept:** “DJDesk Control Room”—a local-first analyst’s cockpit that lets you sync datasets, run Python automations, and interact with the OS without leaving a curated dashboard.

**Why a tutorial app?**
- Gives contributors a tangible end-state to build toward while exercising Django models, views, REST/WebSocket endpoints, and bridging code in Electron.
- Highlights core Electron value props: trusted local storage, OS-level automations, and ergonomic shell access.
- Doubles as canonical documentation: every screen links back to the Read the Docs site, and the doc site embeds GIFs/short clips recorded from the tutorial app.

## 4. Personas & Jobs to Be Done

| Persona | Goal | Tutorial emphasis |
| --- | --- | --- |
| **New contributor** | Understand “why Electron + Django” and get a working desktop build quickly. | Guided setup, in-app helper overlay, simplified data seeding. |
| **Power user / analyst** | Automate local workflows (import CSVs, run scripts, monitor results) without opening multiple apps. | Native file integrations, offline persistence, multi-pane layout. |
| **Documentation consumer** | Follow a cohesive story from Read the Docs and see the same UI in the app. | Deep links from docs to app sections, screenshots recorded per doc page. |

## 5. Feature Pillars

### 5.1 Django-driven flows
- **Workspace onboarding:** wizard for naming a workspace, selecting a local folder, and seeding SQLite fixtures; persists via Django models.
- **Data sync queue:** CRUD views + REST API for datasets (CSV, JSON, SQLite) showing status (pending, running, completed) driven by Django background tasks (Celery later, mocked now).
- **Insights board:** server-rendered API that aggregates dataset metadata (row counts, last sync) and exposes WebSocket updates for in-app live refresh.
- **Task orchestration via `django-tasks`:** integrate https://github.com/RealOrangeOne/django-tasks (already MIT-licensed) so users can dispatch long-running commands with an in-app progress bar. The PRD should specify:
  - A “Run task” flyout wired to the assistant/chat panel that posts jobs to `django-tasks`.
  - Real-time progress indicator (0–100%, log snippets, cancel button) mirrored both in the UI task list and the toolbar status badge.
  - Sample tasks: dataset reindex, CSV validation, `django-admin` custom command. Include at least one tutorial step demonstrating task submission and progress tracking.

### 5.2 Electron-only differentiators

| Capability | Requirement |
| --- | --- |
| **Local file access** | Drag a folder into the app to register it as a “workspace.” Show native file picker and display OS path metadata from Electron’s `dialog` module. |
| **System notifications** | Trigger notifications when a dataset finishes processing or fails; demonstrate bridging from Django (via channel) to Electron `Notification`. |
| **Shell automation** | Let users configure a “post-sync command” (e.g., run `python scripts/analyze.py`). Electron spawns the command locally and streams logs back into the UI. |
| **Offline-ready cache** | App runs fully offline after the first launch. Django uses SQLite; Electron caches static assets; UI surfaces connectivity status. |
| **Window/tray controls** | Provide mini-mode (compact view in a frameless window) and optional tray icon to relaunch background tasks. |

### 5.3 Teaching moments
- Each module surfaces “How this works” links that jump into matching Read the Docs sections.
- Include instrumentation toggles (e.g., intentionally fail a sync) to demonstrate debugging flows inside Electron (DevTools instructions, log locations).

## 6. Tutorial Storyline

| Stage | In-app content | Learning goals |
| --- | --- | --- |
| **0 – Hello World (existing)** | Default Django page served via Electron. | Baseline sanity check. |
| **1 – Guided Workspace Setup** | Multi-step modal explaining the architecture, selecting a data folder, seeding demo data. | Running Django management commands from Electron, persisting settings. |
| **2 – Dashboard & Dataset Explorer** | Split layout (sidebar + hero + cards) inspired by Bayesian Energy; shows current workspace, dataset cards, recent activity feed. | Tailwind/Vite-compiled frontend served by Django, live reload inside Electron. |
| **3 – Native Enhancements** | File drag/drop target, OS notifications, command runner with log streaming, network/offline indicator. | Proves Electron advantages and how to talk to Django via preload bridge. |
| **4 – Task Runner & Help Mode** | Assistant drawer includes “Run task” actions backed by `django-tasks`, shows live progress bar/logs, and links to docs. | Teaches background task orchestration, progress reporting, and documentation tie-ins. |

Each stage should be independently toggleable via feature flags so writers can capture screenshots even if later features are unfinished.

## 7. Visual & Frontend Direction

- **Primary reference:** the Convexity desktop screenshot (blue-on-black IDE-like layout, top toolbar with icons, collapsible left navigation tree, central map canvas, right-side assistant/chat drawer, and a bottom status strip). Treat it as the canonical layout we are emulating, not the marketing site.
- **Screen regions to replicate:**
  - **Global chrome:** 48–56px dark toolbar with icon buttons (File, Toolbox, Starter Models, Docs, Queue) plus Run/Config toggles, matching the screenshot’s hierarchy.
  - **Left rail:** stacked panes for “Networks” tree, directory browser, and workspace metadata. Should support accordion collapse and badge counts exactly like the Convexity UI.
  - **Center canvas:** map/visualization pane with tab strip (`Map`, `Attributes`, `Code`, `Results`). For DJDesk this can render dataset maps (MapLibre/MapTiler) and switch to table/code previews using the same tab affordances.
  - **Right assistant:** “Convexity Assistant” analogue that houses AI/help cards, `django-tasks` runners, and a chat composer with network selector, send button, and quick action buttons. The drawer must surface the progress bar widget tied to running tasks.
  - **Bottom status bar:** show path info, sync status, app version, and quick toggles (theme, account) just like the screenshot’s blue strip.
- **Typography & colors:** stay in the system/IDE palette (Inter or IBM Plex Sans, medium-weight headings, monospace for code). Use navy gradients (#0b1120 → #111c3c), electric blue accents (#377DFF), and subtle glassmorphism cards for assistant buttons.
- **Components & stack:** still recommend React + TypeScript + Vite + Tailwind for layout plus Radix UI for primitives. Add MapLibre GL (or MapTiler SDK) for the central visualization to match the screenshot’s dark map aesthetic. Support high-density iconography (Lucide or Phosphor) for toolbar buttons.
- **Micro-interactions:** focus on workspace-like affordances (dragging tabs, hover states on tree nodes, map zoom/tooltip), rather than marketing scroll effects.

## 8. Documentation Integration Plan

- **Primary deliverable:** create a Sphinx doc set (`docs/` folder) echoing `../django-indieweb/docs` conventions—`index.rst`, `tutorial.rst`, `concepts.rst`, etc.—and publish via Read the Docs.
- **In-app alignment:** each tutorial stage in §6 maps 1:1 to a doc chapter; embed permalinks inside the Electron UI (“View full instructions on Read the Docs”).
- **Recording loop:** add lightweight script (e.g., `just capture-doc-shots`) to render consistent screenshots for docs.
- **Separate spec?** The library-style documentation work (infrastructure, hosting, CI) is broad enough to deserve its own spec. Create `specs/2025-11-16_docs.md` dedicated to doc tooling, while this document focuses on app narrative & content.

## 9. Success Criteria

- Demo build highlights at least three Electron-native capabilities and two Django data flows.
- Tutorial screens mirror the Read the Docs outline; QA reviewers can click through the app following the doc text without confusion.
- Design reviewers acknowledge proximity to the Convexity desktop layout (toolbar hierarchy, dual side panels, dark map canvas, assistant drawer).
- Read the Docs publishes automatically from `main`, showing tutorial content the same day features land.

## 10. Open Questions / Risks

1. **Dataset subject matter:** Should the tutorial ship neutral CSVs (finance, energy, productivity) or be DJDesk-specific data? Pick something that aligns with the control-room narrative.
2. **Background processing:** Will we use Celery/RQ for queued jobs or mimic async work inside Electron for now?
3. **Security constraints:** Any concerns about spawning arbitrary shell commands from Electron? Need guardrails before exposing to users.
4. **`django-tasks` hosting:** Should tasks run in the main Django process or a separate worker? Evaluate resource impact and how to persist logs once packaged.
5. **Design assets:** Do we commission custom illustrations/icons to match the Convexity aesthetic, or lean on free assets?
6. **Doc scope split:** When should the doc-specific spec be drafted relative to this PRD?
