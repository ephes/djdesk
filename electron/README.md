# DJDesk Electron App

This directory contains the Electron desktop application wrapper for DJDesk.

## Current Implementation: Phase 1 - Minimal Shell (Option A)

This is a minimal Electron shell that:
- Spawns the Django dev server using your system Python
- Displays the Django app in an Electron window
- Manages the Django process lifecycle (start/stop)

### Prerequisites

1. **Python 3.14+** must be installed and available in your PATH
2. **DJDesk dependencies** must be installed:
   ```bash
   # From project root
   uv pip install -e .
   ```

3. **Node.js** (v18 or later) must be installed

### Installation

```bash
cd electron
npm install
```

### Running the App

```bash
# From the electron/ directory
npm start
```

This will:
1. Start the Django dev server on an available port (default: 8000)
2. Wait for the server to be ready
3. Open an Electron window showing the Django app

**Using a specific Python interpreter:**
```bash
# Use a custom Python (e.g., pyenv shim)
PYTHON=/path/to/python3.14 npm start
```

The app will automatically try `python3` first, then `python`. Set `PYTHON` to override this behavior.

### Development

- The app runs Django with `--noreload` to prevent conflicts
- Press `Ctrl+C` or close the window to stop both Electron and Django
- Django logs appear in the terminal where you ran `npm start`

## Limitations (Phase 1)

This is a **development-only** implementation:
- ✅ Works great for local development (`npm start`)
- ❌ **Cannot create distributable packages** (requires Phase 2/3)
- ❌ Requires Python 3.14+ and Django installed on the system

## Next Steps

To create distributable applications, we need to implement:

- **Phase 2**: Bundle Python virtualenv + Django project into the app
- **Phase 3**: Include fully relocatable Python interpreter (python-build-standalone)

See `../specs/2025-11-15_django_electron.md` for the full roadmap.

## Architecture

```
electron/
├── main.js         # Main Electron process (spawns Django)
├── preload.cjs     # Security preload script
├── package.json    # Node dependencies
└── README.md       # This file
```

The Django project lives in `../src/djdesk/` and is run via `../manage.py`.

## Troubleshooting

**"Django server failed to start"**
- Ensure Python 3.14+ is installed: `python3 --version` or `python --version`
- Ensure DJDesk is installed: `python3 -c "import django"`
- Check that manage.py works: `python3 ../manage.py runserver`
- Try specifying Python explicitly: `PYTHON=python3.14 npm start`

**Port conflicts**
- The app automatically finds an available port using `get-port`
- If you see port errors, ensure no other Django instances are running

**Window doesn't open**
- Check the terminal for Django errors
- The app waits up to 15 seconds for Django to respond
- Try running Django manually first to diagnose issues
