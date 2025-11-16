from __future__ import annotations

from typing import Any

from django.conf import settings
from django.db import transaction
from django_tasks import task

from .command_runner import (
    CommandExecutionError,
    CommandResult,
    UnsafeCommandError,
    run_command,
    validate_safe_command,
)
from .models import WorkspaceTaskRun


def _store_command_metadata(run: WorkspaceTaskRun, payload: dict[str, Any]) -> dict[str, Any]:
    metadata = run.metadata or {}
    metadata["command"] = payload
    run.metadata = metadata
    run.save(update_fields=["metadata"])
    return metadata["command"]


def _fail_run(
    run: WorkspaceTaskRun,
    message: str,
    *,
    safe_prefix: str | None = None,
) -> dict[str, Any]:
    run.append_log(message)
    run.flush_log_buffer()
    payload: dict[str, Any] = {
        "raw": run.preset.command,
        "workspace_path": run.workspace.project_path,
        "error": message,
    }
    if safe_prefix:
        payload["safe_prefix"] = safe_prefix
    _store_command_metadata(run, payload)
    run.mark_finished(success=False)
    # Ensure the calling view gets deterministic data even inside transactions.
    transaction.on_commit(lambda: None)
    return payload


def _success_payload(run: WorkspaceTaskRun, result: CommandResult) -> dict[str, Any]:
    payload = {
        "raw": run.preset.command,
        "workspace_path": run.workspace.project_path,
        "safe_prefix": result.safe_prefix,
        "exit_code": result.exit_code,
        "duration_seconds": result.duration,
        "output_lines": result.output_lines,
        "timed_out": result.timed_out,
    }
    return _store_command_metadata(run, payload)


@task()
def execute_workspace_task(task_run_id: int) -> dict[str, Any]:
    """Execute a SAFE Django management command for the selected workspace."""

    run = WorkspaceTaskRun.objects.select_related("workspace", "preset").get(pk=task_run_id)
    command = (run.preset.command or "").strip()
    try:
        safe_prefix = validate_safe_command(command)
    except CommandExecutionError as exc:
        return _fail_run(run, str(exc))

    run.mark_running()
    run.progress = 5
    run.save(update_fields=["progress"])
    run.append_log(f"Executing `{command}` inside {run.workspace.project_path}")
    run.flush_log_buffer()

    try:
        result = run_command(
            command=command,
            workspace_path=run.workspace.project_path,
            timeout=settings.INSPECTOR_TASK_TIMEOUT,
            log_callback=run.append_log,
            safe_prefix=safe_prefix,
        )
    except UnsafeCommandError as exc:
        return _fail_run(run, str(exc))
    except CommandExecutionError as exc:
        return _fail_run(run, str(exc), safe_prefix=safe_prefix)

    run.progress = 95
    run.save(update_fields=["progress"])
    if result.timed_out:
        run.append_log("Command timed out before completion.")
        run.flush_log_buffer()
    else:
        run.append_log(f"Command finished with exit code {result.exit_code}.")
        run.flush_log_buffer()

    payload = _success_payload(run, result)
    run.mark_finished(success=(result.exit_code == 0 and not result.timed_out))

    # Ensure the calling view gets deterministic data even inside transactions.
    transaction.on_commit(lambda: None)
    return payload
