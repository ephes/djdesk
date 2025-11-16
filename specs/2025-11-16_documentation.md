# 2025-11-16 — Documentation System (Sphinx + Furo + Read the Docs)

## 1. Purpose

- Stand up a documentation stack that mirrors the maturity of `django-indieweb` but is tailored to DJDesk’s Electron-first narrative.
- Ensure docs can be embedded or deep-linked inside the tutorial Electron app (see `2025-11-16_app_contents.md`), so users see the same content in-app and on the public site.
- Optimize for quick authoring (RST + MyST), consistent theming (Furo), and automated publishing via Read the Docs (RTD).

## 2. Inputs

| Source | Notes |
| --- | --- |
| `../django-indieweb/docs/conf.py` | Confirms the author’s preference for Sphinx + Furo, custom CSS/JS, and Django-aware configuration. |
| `specs/2025-11-16_app_contents.md` | Defines the tutorial storyline, personas, and layout; documentation must map 1:1 to those stages. |
| Current repo state | No `docs/` directory yet, so this spec dictates the entire structure and tooling choices. |

## 3. Deliverables

1. `docs/` tree with starter content (`index.rst`, `tutorial/*.rst`, `concepts.rst`, `architecture.rst`, `electron.rst`, etc.).
2. `docs/conf.py` configured for DJDesk (imports Django settings, uses Furo theme, supports dark mode assets).
3. `docs/requirements.txt` (or `pyproject` extra) pinning Sphinx, Furo, sphinx-autobuild, myst-parser, sphinxcontrib-mermaid.
4. `readthedocs.yml` that installs dependencies via `uv pip install -r docs/requirements.txt`, targets Python 3.13 on RTD until their 3.14 image is public, then bumps to 3.14 to match the rest of the stack.
5. `just docs-html` and `just docs-serve` recipes (wrapping `sphinx-build` and `sphinx-autobuild`) plus CI checks (`just docs-check`).
6. Screenshot pipeline for embedding UI shots referenced from the Electron tutorial (can be stubbed in this phase but must be spec’d).

## 4. Tooling Decisions

| Area | Decision | Rationale |
| --- | --- | --- |
| **Markup** | reStructuredText by default (matching `django-indieweb`), but include `myst-parser` so contributors can author in Markdown where helpful. |
| **Theme** | [Furo](https://pradyunsg.me/furo/) with custom palette matching the Convexity screenshot (navy gradients, electric blue accents). Extend `custom.css` for map imagery + Mermaid contrast. |
| **Extensions** | `sphinx.ext.autodoc`, `sphinx.ext.napoleon`, `sphinx.ext.viewcode`, `sphinx.ext.todo`, `sphinx_copybutton`, `sphinxcontrib.mermaid`, `myst_parser`. |
| **Live reload** | `sphinx-autobuild` invoked via `just docs-serve` for local authoring. |
| **Assets** | Store screenshots/GIFs under `docs/_static/app-shots/<stage>`. Land `just capture-doc-shots` early so feature-flagged screenshots stay in sync with tutorial steps. |
| **Localization** | Out of scope now; keep structure translation-ready by avoiding hard-coded strings in CSS and using Sphinx i18n patterns. |
| **Dependencies** | Stick with `docs/requirements.txt` for RTD compatibility; optionally add a `docs` extra to `pyproject.toml` later for local installs. |

## 5. Documentation Structure

```
docs/
  conf.py
  index.rst
  requirements.txt
  _static/
    custom.css
    mermaid-init.js
    app-shots/
  _templates/
  tutorial/
    00_getting_started.rst
    01_workspace_setup.rst
    02_dashboard.rst
    03_native_enhancements.rst
    04_task_runner.rst
  api/
    overview.rst
    rest_endpoints.rst
    websocket_channels.rst
  concepts.rst
  architecture.rst
  electron.rst
  contributing-docs.rst
  changelog.rst
```

### Mapping to tutorial app

| Doc page | Electron stage | Notes |
| --- | --- | --- |
| `tutorial/00_getting_started` | Stage 0 | Basic Electron + Django hello world checklist. |
| `tutorial/01_workspace_setup` | Stage 1 | Screenshots of onboarding wizard, folder picker instructions. |
| `tutorial/02_dashboard` | Stage 2 | Layout breakdown referencing Convexity-style UI. |
| `tutorial/03_native_enhancements` | Stage 3 | Explains drag/drop, notifications, shell automation. |
| `tutorial/04_task_runner` | Stage 4 | Demonstrates `django-tasks` progress bar, includes API references. |
| `api/overview` | All stages | Entry point for REST/WebSocket docs referenced from assistant panel. |
| `electron.rst` | Cross-links to packaging spec and describes bundling process for contributors. |
| `architecture.rst` | High-level view (Django, Electron, task runner, frontend build). |

Each tutorial page must expose:
- **At-a-glance summary** (bullets).
- **Prerequisites** (feature flags, test data).
- **Step-by-step** with callouts referencing UI element IDs.
- **Deep link** anchor for in-app “View Docs” button (e.g., `/tutorial/03_native_enhancements/#notifications`).

## 6. RTD & CI Plan

1. **`readthedocs.yml`**
   ```yaml
   version: 2
   build:
     os: ubuntu-22.04
      tools:
        python: "3.13"
   python:
     install:
       - requirements: docs/requirements.txt
   sphinx:
     configuration: docs/conf.py
   ```
   - DJDesk standardizes on Python 3.14 across backend + Electron bundle. RTD hasn’t exposed 3.14 yet, so we temporarily pin builds to 3.13; once 3.14 appears in their tool list, update `readthedocs.yml` accordingly.
2. **Secrets**: no secrets needed; RTD only requires linking the GitHub repo.
3. **Branch policy**: publish `latest` from `main`, `stable` from the most recent tagged release.
4. **GitHub Actions sanity check**: add `docs.yml` workflow to run `just docs-html` on pull requests so RTD failures are caught earlier.
5. **Linking**: add RTD badge to `README.md` once the project builds successfully.

## 7. Integration with Electron App

- Add `docs_manifest.json` (generated during doc build) listing doc slugs + anchor hashes. Electron can load this to provide “open docs” deeplinks.
- Provide `DOCS_BASE_URL` env var (defaulting to `https://djdesk.readthedocs.io/en/latest/`) so in-app links are configurable.
- For offline access, consider bundling a trimmed static snapshot of the tutorial pages; flag as future work but ensure doc build can output to `electron/django-bundle/docs`.
- The assistant drawer (Stage 4) should call a lightweight API that returns doc snippets; plan for this by generating `docs/search.json` via `sphinx-build -b json`.

## 8. Writing & Review Guidelines

- Maintain consistent “control room” voice; use second-person imperative for tutorials (“Click **Run Task**”).
- Every page must include a “Troubleshooting” section with log locations (Electron console, Django logs).
- Use Mermaid for architecture diagrams, storing theme overrides in `_static/mermaid-init.js`.
- When referencing commands, prefer `uv run`/`just` so docs stay aligned with repo tooling.
- Always document the “latest” feature set; when a step depends on a feature flag, call it out explicitly (e.g., “Enable `FEATURE_TASK_RUNNER` to see this panel”).

## 9. Timeline & Milestones

1. **Week 1**: Bootstrap `docs/`, add minimal content (index + Stage 0/1 tutorials), configure RTD + build workflow, and script `just capture-doc-shots` so screenshots ship with copy from day one.
2. **Week 2**: Flesh out tutorial pages 2–4 with refreshed screenshots, add architecture/electron chapters, and land API docs skeleton.
3. **Week 3**: Wire docs manifest export for Electron integration and harden screenshot automation (CI hooks, linting of metadata files).
4. **Beyond**: Localization/I18N, offline snapshotting, doc site analytics.

RTD builds rely on screenshots committed to the repo; we will not attempt to spin up Electron or seed demo data inside RTD’s sandbox.

## 10. Open Questions

1. Should we store doc dependencies inside `pyproject` extras instead of `docs/requirements.txt`?
2. If we later want CI-managed screenshots, what infra would safely drive Electron headlessly without violating RTD constraints?
3. Do we need a separate landing page for documentation (e.g., marketing hero) or is Furo’s default landing adequate?
4. What automation ensures docs stay accurate when feature flags move (lint linking `FEATURE_*` references, etc.)?
5. Is there appetite for interactive code sandboxes (Pyodide, embedded repl) inside the docs, or do we keep them static initially?
