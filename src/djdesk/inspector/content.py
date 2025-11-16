from __future__ import annotations

TASK_PRESETS = [
    {
        "key": "showmigrations",
        "label": "Diff migrations",
        "description": "Compare applied vs. pending migrations across every Django app.",
        "command": "python manage.py showmigrations",
        "docs_url": "https://djdesk.readthedocs.io/en/latest/tutorial/03_native_enhancements.html",
        "category": "schema",
        "icon": "git-branch",
        "order": 10,
    },
    {
        "key": "check",
        "label": "Run system checks",
        "description": "Execute Django's `check` command to verify settings and models.",
        "command": "python manage.py check",
        "docs_url": "https://djdesk.readthedocs.io/en/latest/tutorial/04_task_runner.html",
        "category": "diagnostics",
        "icon": "shield-check",
        "order": 20,
    },
    {
        "key": "diffsettings",
        "label": "Diff settings",
        "description": "Highlight deviating settings between local and production profiles.",
        "command": "python manage.py diffsettings",
        "docs_url": "https://djdesk.readthedocs.io/en/latest/concepts.html",
        "category": "configuration",
        "icon": "sliders",
        "order": 30,
    },
    {
        "key": "inspectdb",
        "label": "Inspect database",
        "description": "Reverse-engineer models from the connected database schema.",
        "command": "python manage.py inspectdb",
        "docs_url": "https://djdesk.readthedocs.io/en/latest/tutorial/02_dashboard.html",
        "category": "schema",
        "icon": "database",
        "order": 40,
    },
    {
        "key": "sqlmigrate",
        "label": "Show SQL for migrations",
        "description": (
            "Render the SQL for the next pending migration as part of the "
            "inspection report."
        ),
        "command": "python manage.py sqlmigrate core 0003",
        "docs_url": "https://djdesk.readthedocs.io/en/latest/api/rest_endpoints.html",
        "category": "schema",
        "icon": "scroll",
        "order": 50,
    },
    {
        "key": "dumpdata",
        "label": "Export fixtures",
        "description": (
            "Create JSON fixtures for the selected workspace without mutating "
            "the source project."
        ),
        "command": "python manage.py dumpdata --natural-foreign --indent=2",
        "docs_url": "https://djdesk.readthedocs.io/en/latest/tutorial/04_task_runner.html#fixture-export",
        "category": "automation",
        "icon": "package",
        "order": 60,
    },
]

DOC_LINKS = [
    {
        "slug": "stage-0-hello",
        "title": "Stage 0 — Hello World",
        "description": "Verify the Electron shell can display Django locally.",
        "url": "https://djdesk.readthedocs.io/en/latest/tutorial/00_getting_started.html",
        "stage": 0,
        "category": "tutorial",
        "icon": "play",
        "pane_target": "hello",
    },
    {
        "slug": "stage-1-import",
        "title": "Stage 1 — Import Project",
        "description": "Run the onboarding wizard and capture screenshots of the workspace cards.",
        "url": "https://djdesk.readthedocs.io/en/latest/tutorial/01_workspace_setup.html",
        "stage": 1,
        "category": "tutorial",
        "icon": "upload",
        "pane_target": "wizard",
    },
    {
        "slug": "stage-2-explorer",
        "title": "Stage 2 — Project Explorer",
        "description": "Explain the Convexity-inspired split view and schema canvas.",
        "url": "https://djdesk.readthedocs.io/en/latest/tutorial/02_dashboard.html",
        "stage": 2,
        "category": "tutorial",
        "icon": "layout-grid",
        "pane_target": "schema",
    },
    {
        "slug": "stage-3-native",
        "title": "Stage 3 — Native Enhancements",
        "description": "Document drag/drop project import, offline caching, and notifications.",
        "url": "https://djdesk.readthedocs.io/en/latest/tutorial/03_native_enhancements.html",
        "stage": 3,
        "category": "tutorial",
        "icon": "bell",
        "pane_target": "native",
    },
    {
        "slug": "stage-4-runner",
        "title": "Stage 4 — Task Runner",
        "description": "Walk through the `django-tasks` integration and assistant drawer.",
        "url": "https://djdesk.readthedocs.io/en/latest/tutorial/04_task_runner.html",
        "stage": 4,
        "category": "tutorial",
        "icon": "terminal",
        "pane_target": "tasks",
    },
]

WORKSPACE_FIXTURES = [
    {
        "name": "Atlas Telemetry Studio",
        "project_path": "~/Projects/sample_projects/atlas",
        "python_version": "3.14.0",
        "django_version": "5.2.8",
        "docs_url": "https://djdesk.readthedocs.io/en/latest/",
        "description": "Local-first insights cockpit that mirrors the tutorial storyline.",
        "manage_py_detected": True,
        "last_scan_at": "2025-11-16T15:30:00+00:00",
        "metadata": {
            "status": {
                "project": "~/Projects/sample_projects/atlas",
                "mode": "Offline ready",
                "profile": "Local studio",
                "docs": "Stage 2 — Project Explorer",
            },
            "insights": [
                {
                    "title": "Pending migrations",
                    "value": "3",
                    "delta": "-1 vs last sync",
                    "severity": "warning",
                    "caption": "catalog.datasets · ledger.entries",
                    "icon": "git-branch",
                },
                {
                    "title": "Apps discovered",
                    "value": "8",
                    "delta": "+2 new apps",
                    "severity": "info",
                    "caption": "core, catalog, alerts, ledger, ops",
                    "icon": "layers",
                },
                {
                    "title": "Automation scripts",
                    "value": "5",
                    "delta": "ready to run",
                    "severity": "success",
                    "caption": "checks, fixture export, schema capture",
                    "icon": "sparkles",
                },
            ],
            "apps": [
                {
                    "label": "catalog.datasets",
                    "models": 6,
                    "pending_migrations": 1,
                    "status": "warning",
                    "color": "#ffb020",
                },
                {
                    "label": "alerts.monitors",
                    "models": 4,
                    "pending_migrations": 0,
                    "status": "success",
                    "color": "#34d399",
                },
                {
                    "label": "ledger.entries",
                    "models": 5,
                    "pending_migrations": 1,
                    "status": "warning",
                    "color": "#f97316",
                },
                {
                    "label": "ops.telemetry",
                    "models": 9,
                    "pending_migrations": 0,
                    "status": "info",
                    "color": "#60a5fa",
                },
            ],
            "recent_activity": [
                {
                    "timestamp": "2025-11-16T15:25:00+00:00",
                    "kind": "scan",
                    "message": "Schema scan completed in 18s",
                    "status": "success",
                },
                {
                    "timestamp": "2025-11-16T15:22:00+00:00",
                    "kind": "task",
                    "message": "django check passed",
                    "status": "success",
                },
                {
                    "timestamp": "2025-11-16T15:19:00+00:00",
                    "kind": "import",
                    "message": "Log tail synced 250 entries",
                    "status": "info",
                },
            ],
            "schema": {
                "nodes": [
                    {
                        "name": "Dataset",
                        "fields": ["id", "slug", "ingested_at", "status"],
                        "relations": ["Snapshot", "Owner"],
                        "badge": "catalog",
                    },
                    {
                        "name": "Snapshot",
                        "fields": ["id", "dataset_id", "checksum", "captured_at"],
                        "relations": ["Dataset"],
                        "badge": "catalog",
                    },
                    {
                        "name": "Owner",
                        "fields": ["id", "email", "timezone"],
                        "relations": ["Dataset"],
                        "badge": "accounts",
                    },
                    {
                        "name": "Monitor",
                        "fields": ["id", "slug", "threshold", "frequency"],
                        "relations": ["Dataset"],
                        "badge": "alerts",
                    },
                ],
                "connections": [
                    {"source": "Dataset", "target": "Snapshot"},
                    {"source": "Dataset", "target": "Owner"},
                    {"source": "Dataset", "target": "Monitor"},
                ],
            },
            "log_excerpt": [
                {
                    "timestamp": "15:25:18",
                    "level": "info",
                    "message": "Schema ingest finished.",
                },
                {
                    "timestamp": "15:22:01",
                    "level": "info",
                    "message": "Running system checks...",
                },
                {
                    "timestamp": "15:22:03",
                    "level": "success",
                    "message": "System check identified 0 issues.",
                },
                {
                    "timestamp": "15:19:44",
                    "level": "info",
                    "message": "Streaming logs from runserver.",
                },
            ],
        },
        "scans": [
            {
                "kind": "schema",
                "status": "completed",
                "summary": "Discovered 24 models across 8 apps",
                "progress": 100,
                "log_excerpt": "Graph built with React Flow export.",
            },
            {
                "kind": "migrations",
                "status": "running",
                "summary": "Reviewing unapplied migrations",
                "progress": 55,
            },
            {
                "kind": "logs",
                "status": "pending",
                "summary": "Ready to import local logs",
                "progress": 0,
            },
        ],
        "task_runs": [
            {
                "preset": "check",
                "status": "succeeded",
                "progress": 100,
                "log": (
                    "[15:22:01] Running django check\n"
                    "[15:22:03] System check identified 0 issues."
                ),
            },
            {
                "preset": "showmigrations",
                "status": "succeeded",
                "progress": 100,
                "log": "[15:21:00] Inspecting migrations\n[15:21:04] Found 3 pending migrations.",
            },
        ],
    }
]

TASK_SIMULATIONS = {
    "showmigrations": {
        "summary": "Pending migrations identified across analytics apps.",
        "steps": [
            {"progress": 5, "message": "Loading app registry..."},
            {"progress": 25, "message": "Parsing migration graph..."},
            {"progress": 70, "message": "Comparing applied vs. unapplied nodes..."},
            {"progress": 100, "message": "Found drift in catalog.datasets and ledger.entries."},
        ],
        "payload": {"pending": 3, "apps": ["catalog.datasets", "ledger.entries"]},
    },
    "check": {
        "summary": "django check completed with no issues.",
        "steps": [
            {"progress": 10, "message": "Running Django system checks..."},
            {"progress": 60, "message": "Inspecting models and settings..."},
            {"progress": 100, "message": "System check identified 0 issues."},
        ],
        "payload": {"errors": 0, "warnings": 0},
    },
    "diffsettings": {
        "summary": "Highlighted overrides relative to production.",
        "steps": [
            {"progress": 15, "message": "Loading base settings..."},
            {"progress": 55, "message": "Comparing with target profile..."},
            {"progress": 100, "message": "Diff ready for review."},
        ],
        "payload": {"differences": ["DEBUG=True", "ALLOWED_HOSTS=['*']"]},
    },
    "inspectdb": {
        "summary": "Generated 12 candidate models from the live database.",
        "steps": [
            {"progress": 20, "message": "Reflecting database schema..."},
            {"progress": 75, "message": "Mapping fields to Django types..."},
            {"progress": 100, "message": "Schema ready. Save as `schema.py`."},
        ],
        "payload": {"models": 12},
    },
    "sqlmigrate": {
        "summary": "Rendered SQL for catalog 0003.",
        "steps": [
            {"progress": 30, "message": "Loading migration file..."},
            {"progress": 80, "message": "Expanding operations..."},
            {"progress": 100, "message": "SQL emitted for catalog.0003_optional_fields."},
        ],
        "payload": {"migration": "catalog.0003_optional_fields"},
    },
    "dumpdata": {
        "summary": "Serialized 1,240 rows into fixture JSON.",
        "steps": [
            {"progress": 15, "message": "Collecting models from allowlist..."},
            {"progress": 65, "message": "Streaming queryset rows..."},
            {"progress": 100, "message": "Fixture written to storage/app-fixtures.json."},
        ],
        "payload": {"rows": 1240, "path": "storage/app-fixtures.json"},
    },
}
