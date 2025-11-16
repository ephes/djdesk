# 2025-11-15 — Django + Electron Packaging Research

Goal: determine how to wrap the current `djdesk` startproject in an Electron shell that can be shipped for Windows, macOS and Linux, with minimal friction for a “hello world” Django screen today and a path to a fully offline, self-contained experience later.

---

## Confirmed Requirements (as of 2025-11-16)

- **Python 3.14** is a hard requirement; we will not downgrade to 3.13 just because tooling is easier.
- **Public downloads** are the target distribution, so later phases must deliver fully bundled binaries with no external prerequisites.
- **Auto-update** decisions are deferred to a dedicated spec (manual download/install for now).
- **Static assets** may continue to be served by Django’s built-in dev server initially; WhiteNoise or CDN alternatives can be postponed.
- **Background workers** are out of scope for the first milestone.
- **Secrets/config** storage inside the Electron bundle is not needed yet.
- **Interpreter sourcing**: we rely on the published `python-build-standalone` 3.14 artifacts for all platforms; we will not build CPython ourselves.

These constraints drive the rest of this document (e.g., insisting on Option C despite the complexity).

---

## 1. Current Baseline (DJDesk)

- Thin Django startproject that lives under `src/djdesk` with `manage.py` in the repo root. There is no frontend build tooling or Electron code yet (`manage.py`:1-29, `src/djdesk`).
- Python dependency graph is tiny: `pyproject.toml` only installs Django ≥5.2.8 and targets Python 3.14+ (`pyproject.toml`:1-8). No native deps, no background workers, no static asset pipeline beyond Django defaults.
- No specs exist yet in `specs/`, so this document acts as the first issue-tracking record for the Electron effort.

Early implication: the initial Electron wrapper can be very small (even just showing the default Django start page) while we figure out how much of the steel-model approach we really need.

---

## 2. Reference Implementation (steel-model)

Studying `../steel-model` is instructive because it already solves “run Django inside Electron” for a much larger system:

1. **Docs:** `docs/development/electron_app.md` explains the desktop app goals, build commands, icon handling, and CI prerequisites (`../steel-model/docs/development/electron_app.md`:7-134).
2. **Electron package scripts:** `src/electron/package.json` wires `npm run build` to `node build-django.js && electron-builder` so bundling happens before packaging (`../steel-model/src/electron/package.json`:1-32).
3. **Bundling script:** `src/electron/build-django.js` copies server code into `django-bundle`, installs dependencies with `uv`, and creates portable Python roots (especially the elaborate Windows DLL copying) before Electron grabs it (`../steel-model/src/electron/build-django.js`:1-200 and beyond).
4. **Packaging config:** `src/electron/electron-builder.json` registers `django-bundle` as `extraResources`, enables ASAR, and points to platform-specific icon files (`../steel-model/src/electron/electron-builder.json`:1-49).
5. **CI:** `.github/workflows/standalone_app.yaml` runs a 3-OS matrix, uses `astral-sh/setup-uv`, downloads a relocatable interpreter for macOS, and calls `npm run build` before uploading artifacts (`../steel-model/.github/workflows/standalone_app.yaml`:1-190).

This stack achieves:
- Local Django server spawned by Electron with its own SQLite DB.
- All Python deps frozen inside the app.
- GitHub Actions artifacts for Win/macOS/Linux without needing developer machines for each OS.

But it is heavy: copying DLLs, ~1 GB bundles, S3 upload logic, Sentry integration, etc. DJDesk can adopt the same architecture incrementally instead of wholesale.

---

## 3. Packaging Options for DJDesk

| Option | Description | Pros | Cons / Risks | When to choose |
| --- | --- | --- | --- | --- |
| **A. Developer Shell (no bundled Python)** | Electron spawns the system Python (`python3` or a checked virtualenv) to run `manage.py runserver --noreload`, similar to how you develop locally. The Electron app is basically a WebView pointing at `http://127.0.0.1:<port>`. | Fastest path to “Electron shows Django start page”. Users can inspect server logs easily because they live outside the bundle. Works with the single dependency set we have now. | Not redistributable: assumes Python is available and dependencies are installed. Not acceptable for non-technical users. Hard to ship via GitHub Actions because there’s no portable runtime. | Internal experimentation, prototypes, or if we only need Electron as a browser chrome while continuing to run Django separately. |
| **B. Bundled virtualenv per platform** | Ship a `.venv` created via `uv pip install --target ...` (or `python -m venv`) inside `django-bundle/python`. Electron runs `python` from that directory. Similar to steel-model Windows flow but without copying raw interpreters. | Self-contained dependencies; far less effort than extracting `python-build-standalone`. Works if we are okay requiring the OS-provided base interpreter (e.g., system Python on macOS/Linux, the action-installed interpreter on Windows). | Not fully relocatable: virtualenvs often break if moved across machines; Linux AppImage might miss glibc compatibility; Windows still needs VC++ runtimes. Hard to guarantee offline capability. | For a “Phase 1 shipping build” where we only target a handful of internal testers and can tolerate occasional interpreter drift. |
| **C. Fully relocatable interpreter (python-build-standalone)** | Copy the steel-model approach: download the official `python-build-standalone` 3.14 tarballs per OS, unpack them inside `django-bundle/python`, and install our project into that prefix via `uv`. Keeps executables + libs together. | True offline install, no system dependencies, deterministic Python version across OSes. Works nicely in GitHub Actions, matches steel-model CI job, easiest future path for updates / Sentry / worker processes. Required for public distribution because we cannot assume end users have Python 3.14 installed. | Setup scripts are more complex (copy DLLs, patch `PATH`, handle certificates). Larger downloads. Need to mirror the per-OS intricacies from `build-django.js`, but we do **not** build CPython ourselves—only use the published 3.14 archives. | When we want to publish binary releases for the public, match steel-model release process, or require native dependencies later. |

Given DJDesk’s small dependency graph, Option A is enough for verifying the Electron shell, Option B is a stepping stone if we want to demo “standalone” behavior soon, and Option C is the eventual goal for public releases + GitHub Actions artifacts.

---

## 4. Suggested Project Structure

```
djdesk/
├── src/
│   └── djdesk/...
├── electron/
│   ├── package.json
│   ├── main.js
│   ├── preload.cjs
│   ├── build-django.js   # or simpler script for Option A/B
│   ├── electron-builder.json
│   └── django-bundle/    # generated
└── specs/2025-11-15_django_electron.md
```

Notes:
- Keep Electron code under `electron/` (vs `src/electron/` in steel-model) to avoid confusion with Django’s `src/`.
- Point `electron/main.js` to `../manage.py` for Option A, and later to `electron/django-bundle/python` for options B/C.
- `django-bundle` should contain: copied Django project, staticfiles, `.env` (if any), SQLite DB, and `python/` interpreter when relevant.

---

## 5. Implementation Plan

### Phase 0 — Preconditions
1. Update Django settings so the default site is runnable offline:
   - Configure `ALLOWED_HOSTS = ["127.0.0.1", "localhost"]`.
   - Enable SQLite (default) and `STATIC_ROOT = BASE_DIR / "staticfiles"`.
   - Static assets can continue to be served via Django’s own runserver during early phases; WhiteNoise/CDN-based serving is optional future work.
2. Decide on the Python version to package. Steel-model pins 3.13.8 for reproducibility (`../steel-model/src/electron/build-django.js`:24-28). DJDesk is pinned to 3.14, and the corresponding `python-build-standalone` release artifacts are available, so every script/workflow should download those archives rather than building CPython.

### Phase 1 — Minimal Electron Shell (Option A) ✅ Completed
1. Scaffolded `electron/package.json` with only `electron` as a dev dependency and an `npm start` script.
2. Implemented `electron/main.js` that spawns `manage.py runserver` on a random port (using `get-port`), waits for readiness, logs output, and shuts the child down cleanly.
3. Documented the workflow (`electron/README.md` and root `README.md`) plus added `just electron-install` / `just electron-start`.
4. `electron-builder` and build scripts intentionally kept out until bundling support exists.

### Phase 2 — Self-contained Bundles (Option B) ✅ Completed
1. Write a simplified `electron/build-django.js`:
   - Runs `uv pip install --python <system python> -r pyproject.toml --target electron/django-bundle/python/lib/pythonX.Y/site-packages`.
   - Copies `manage.py`, `src/djdesk`, and static assets to `django-bundle`.
   - Runs `python manage.py collectstatic --clear` targeting the bundle.
   - Writes a small launcher script (e.g., `run_django.py`) inside the bundle so Electron can call `python run_django.py`.
2. Modify `electron/main.js` to prefer `django-bundle/python/bin/python` when it exists, else fall back to system Python.
3. Extend `electron-builder.json` to add `django-bundle` via `extraResources` (mirroring steel-model’s config).
4. Add cleanup + hashing to detect stale bundles (optional: embed Git SHA into `django-bundle/VERSION`).

### Phase 3 — Fully relocatable interpreter (Option C) ✅ Completed
1. Ported the bundling script to download python-build-standalone 3.14.0+20251031 for macOS, Linux, and Windows (arm64/x64) and copy it into `django-bundle/python`.
2. All dependency installation happens inside the downloaded interpreter via `uv pip install`.
3. Added verification that Django imports successfully inside the relocatable interpreter.
4. CI/docs updates now emphasize that `npm run bundle`/`npm run build` no longer require any system-wide Python installation.

### Phase 4 — Polishing
- Icons: follow the png→icns/ico flow from `../steel-model/docs/development/electron_app.md`:17-64 to keep builder happy.
- Crash resilience + single-instance locks like `main.js` already uses in steel-model (avoid double-run of Django).
- Telemetry: optional Sentry integration if needed (steel-model loads DSNs from `django-bundle/sentry-config.json`).
- Auto-update / instrumentation, if we want to adopt electron-builder publish assets later.

---

## 6. GitHub Actions Plan

Model the workflow on `.github/workflows/standalone_app.yaml` (`../steel-model/.github/workflows/standalone_app.yaml`:1-190):

1. Matrix of `windows-latest`, `macos-latest`, `ubuntu-latest`. Each job:
   - Checks out repo, sets working directory to `electron/`.
   - Installs Node (v22 or chosen version) and caches npm modules.
   - Installs UV (if we rely on UV for bundling) before running bundler script.
2. Platform setup:
   - macOS: download the published `python-build-standalone` 3.14 tarball, `uv python pin <path>`, ensure execute permission (lines 48-75).
   - Windows: use `actions/setup-python` and `uv python pin 3.14` (lines 58-88) so we capture the official 3.14 binaries from the release feed.
   - Linux: install packaging dependencies (`libfuse2`, `patchelf`, etc.) before building AppImage (lines 41-47), then `uv python pin 3.14`.
3. Build:
   - `npm ci`
   - `npm run build` (which should run `node build-django.js` + `electron-builder`).
4. Verification:
   - Optionally copy the verification shell blocks from steel-model (checking bundle presence, verifying DLLs, static vendor files). For DJDesk, we can simplify: ensure `django-bundle/django` exists and `python/Scripts/python.exe` exists on Windows.
5. Artifacts:
   - Upload zipped `dist/<platform>` directories per OS.
   - If desired, publish to GitHub Releases or a storage bucket after manual approval.

For early iterations we can run the workflow manually (`workflow_dispatch`) like steel-model does; once stable, trigger on tags.

---

## 7. Clarified vs. Open Questions

**Decisions locked in**
1. **Python version:** stay on 3.14; document any friction it causes.
2. **Distribution scope:** public binaries, so Phase 3 (Option C) becomes mandatory in the roadmap.
3. **Auto-updates:** defer to a separate spec; assume manual downloads for now.
4. **Static assets:** Django’s built-in server is good enough initially.
5. **Background workers:** out of scope for this milestone.
6. **Secrets/config:** none bundled.

**Remaining open topics**
1. **Installer signing:** determine macOS notarisation + Windows code-signing requirements before public release.
2. **Option B lifespan:** decide whether to keep a “bundled virtualenv” intermediate once Option C ships.

---

## 8. Next Actions Checklist

- [x] Phase 1 scaffolding and `main.js` implementation (dev-only shell).
- [x] Prototype `build-django.js` for Option B (bundled venv) so we can learn about copying Django/static assets.
- [x] Add a bundle verification step (e.g., import Django inside the virtualenv) so `npm run bundle` fails fast when dependencies break.
- [x] Expand `electron-builder.json` with macOS/Windows/Linux targets and rehearse `npm run build` locally to confirm Option B bundles survive packaging.
- [x] Add platform-specific `just build-linux`, `just build-macos`, `just build-windows` recipes that call the same commands we plan to run in CI so contributors can rehearse builds locally.
- [x] Stand up a first-pass GitHub Actions workflow that runs `npm ci`, `npm run bundle`, and `npm run build` across the 3 OS matrix (manual trigger is OK for now).
- [ ] Mirror key documentation sections (build steps, icon generation) into this repo’s `docs/` once the proof-of-concept is ready.

---

## 9. References

- `../steel-model/docs/development/electron_app.md`:7-164 — overview of the existing Electron workflow, icon pipeline, and CI prerequisites.
- `../steel-model/src/electron/package.json`:1-32 — shows how bundling + packaging scripts chain together.
- `../steel-model/src/electron/build-django.js`:1-200 — demonstrates copying Django sources, installing dependencies via UV, and preparing portable Python directories.
- `../steel-model/src/electron/electron-builder.json`:1-49 — example electron-builder configuration for bundling `django-bundle` as an extra resource with platform icons.
- `../steel-model/.github/workflows/standalone_app.yaml`:1-190 — CI matrix that builds the Electron app on Windows/macOS/Linux with a relocatable interpreter.
- `manage.py`:1-29 and `pyproject.toml`:1-8 in this repo — confirm current Django entry point and dependency scope we are packaging.
