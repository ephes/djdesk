from __future__ import annotations

from django.db import migrations
from django.utils.dateparse import parse_datetime

from djdesk.inspector import content


def seed_initial_content(apps, schema_editor) -> None:
    Workspace = apps.get_model("inspector", "Workspace")
    ScanJob = apps.get_model("inspector", "ScanJob")
    TaskPreset = apps.get_model("inspector", "TaskPreset")
    WorkspaceTaskRun = apps.get_model("inspector", "WorkspaceTaskRun")
    DocLink = apps.get_model("inspector", "DocLink")

    for preset_data in content.TASK_PRESETS:
        TaskPreset.objects.update_or_create(
            key=preset_data["key"],
            defaults={
                "label": preset_data["label"],
                "description": preset_data["description"],
                "command": preset_data["command"],
                "docs_url": preset_data["docs_url"],
                "category": preset_data["category"],
                "icon": preset_data["icon"],
                "order": preset_data["order"],
            },
        )

    for link in content.DOC_LINKS:
        DocLink.objects.update_or_create(
            slug=link["slug"],
            defaults={
                "title": link["title"],
                "description": link["description"],
                "url": link["url"],
                "stage": link["stage"],
                "category": link["category"],
                "icon": link["icon"],
            },
        )

    for fixture in content.WORKSPACE_FIXTURES:
        metadata = fixture.get("metadata", {})
        scans = fixture.get("scans", [])
        task_runs = fixture.get("task_runs", [])

        workspace, _ = Workspace.objects.update_or_create(
            name=fixture["name"],
            defaults={
                "project_path": fixture["project_path"],
                "python_version": fixture["python_version"],
                "django_version": fixture.get("django_version", ""),
                "description": fixture.get("description", ""),
                "docs_url": fixture.get("docs_url", ""),
                "metadata": metadata,
                "manage_py_detected": fixture.get("manage_py_detected", False),
            },
        )

        last_scan_at = fixture.get("last_scan_at")
        if last_scan_at:
            parsed = parse_datetime(last_scan_at)
            if parsed:
                workspace.last_scan_at = parsed
                workspace.save(update_fields=["last_scan_at"])

        if not workspace.scans.exists():
            for scan in scans:
                ScanJob.objects.create(
                    workspace=workspace,
                    kind=scan["kind"],
                    status=scan["status"],
                    summary=scan.get("summary", ""),
                    progress=scan.get("progress", 0),
                    log_excerpt=scan.get("log_excerpt", ""),
                )

        if not workspace.task_runs.exists():
            for run in task_runs:
                preset = TaskPreset.objects.filter(key=run["preset"]).first()
                if not preset:
                    continue
                WorkspaceTaskRun.objects.create(
                    workspace=workspace,
                    preset=preset,
                    status=run.get("status", "succeeded"),
                    progress=run.get("progress", 0),
                    log=run.get("log", ""),
                )


def remove_seeded_content(apps, schema_editor) -> None:
    Workspace = apps.get_model("inspector", "Workspace")
    TaskPreset = apps.get_model("inspector", "TaskPreset")
    DocLink = apps.get_model("inspector", "DocLink")

    Workspace.objects.all().delete()
    TaskPreset.objects.all().delete()
    DocLink.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("inspector", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_initial_content, remove_seeded_content),
    ]
