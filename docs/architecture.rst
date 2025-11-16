Architecture Overview
=====================

Describe how the Electron shell, bundled Python runtime, Django services, and frontend
assets cooperate. Include Mermaid diagrams referencing the build + runtime pipeline
from ``specs/2025-11-15_django_electron.md`` once screenshots are ready.

Key sections to expand:

1. Process model (Electron main, preload bridge, Django server, task runner)
2. Data flow for the dataset queue + `django-tasks`
3. Frontend build pipeline (Vite/Tailwind assets served by Django)
