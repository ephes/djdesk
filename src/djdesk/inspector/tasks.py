from __future__ import annotations

from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django_tasks import task

from .command_runner import (
    CommandExecutionError,
    CommandResult,
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
    payload: dict[str, Any] = {
        "raw": run.preset.command,
        "workspace_path": run.workspace.project_path,
        "error": message,
        "output_limited": getattr(run, "_log_truncated", False) or "Output truncated" in message,
    }
    if safe_prefix:
        payload["safe_prefix"] = safe_prefix
    try:
        run.append_log(message)
        run.flush_log_buffer()
    except Exception:
        # A broken sink cannot prevent failure state from being persisted.
        log = "\n".join([run.log, *getattr(run, "_log_buffer", []), message])
        log = log.encode("utf-8")[: settings.INSPECTOR_TASK_LOG_BYTES].decode(
            "utf-8", errors="ignore"
        )
        metadata = {**(run.metadata or {}), "command": payload}
        WorkspaceTaskRun.objects.filter(pk=run.pk).update(
            status=WorkspaceTaskRun.Status.FAILED,
            progress=100,
            completed_at=timezone.now(),
            metadata=metadata,
            log=log,
        )
        return payload
    _store_command_metadata(run, payload)
    run.mark_finished(success=False)
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
        "output_limited": result.output_limited,
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

    try:
        run.mark_running()
        run.progress = 5
        run.save(update_fields=["progress"])
        run.append_log(f"Executing `{command}` inside {run.workspace.project_path}")
        run.flush_log_buffer()
        result = run_command(
            command=command,
            workspace_path=run.workspace.project_path,
            timeout=settings.INSPECTOR_TASK_TIMEOUT,
            log_callback=run.append_log,
            safe_prefix=safe_prefix,
        )
        run.progress = 95
        run.save(update_fields=["progress"])
        if result.output_limited:
            run.append_log("Command failed because the output byte budget was exhausted.")
        elif result.timed_out:
            run.append_log("Command timed out before completion.")
        else:
            run.append_log(f"Command finished with exit code {result.exit_code}.")
        run.flush_log_buffer()
        payload = _success_payload(run, result)
        run.mark_finished(
            success=(result.exit_code == 0 and not result.timed_out and not result.output_limited)
        )
        transaction.on_commit(lambda: None)
        return payload
    except CommandExecutionError as exc:
        return _fail_run(run, str(exc), safe_prefix=safe_prefix)
    except Exception as exc:
        return _fail_run(run, f"{type(exc).__name__}: {exc}", safe_prefix=safe_prefix)
