Stage 1 — Workspace Setup
=========================

Stage 1 introduces the onboarding wizard that imports an existing Django project and
records metadata without mutating the source repo. The wizard lives at
``/wizard/`` and is surfaced via the toolbar "Import Project" button.

Wizard flow
-----------

1. **Name + description** — stored on :class:`djdesk.inspector.models.Workspace`.
   Slugs are generated automatically and become the URL fragment for API calls.
2. **Locate project** — the wizard validates that the provided folder exists and
   contains ``manage.py``. Inside Electron the file picker returns the real OS path,
   but you can also paste a path manually. ``Workspace.manage_py_detected`` is set
   to ``True`` when validation succeeds.
3. **Docs & automation** — optional docs URL (used by the assistant drawer) plus
   the "auto run scans" toggle. When enabled, :func:`djdesk.inspector.services.bootstrap_workspace_scans`
   seeds the schema/migration/log/fixture scans immediately.

Every submission appends an audit entry to ``workspace.metadata["recent_activity"]`` so
Stage 2 screenshots always show a "Workspace imported via wizard" event.

CLI alternative
---------------

The wizard uses Django forms so it's trivial to drive it via CLI for automated tests:

.. code-block:: python

    from djdesk.inspector.forms import WorkspaceWizardForm

    form = WorkspaceWizardForm(
        data={
            "name": "CI Workspace",
            "project_path": "/tmp/project",
            "python_version": "3.14.0",
            "django_version": "5.2.8",
            "description": "Automated build",
            "docs_url": "https://djdesk.readthedocs.io/en/latest/",
            "auto_run_scan": True,
            "confirm_readonly": True,
        }
    )
    form.is_valid()
    workspace = form.save()

Electron uses the same form POST, so screenshots captured during this stage mirror the
doc instructions exactly.
