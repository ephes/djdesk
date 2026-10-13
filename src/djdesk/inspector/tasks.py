from __future__ import annotations

import logging
import sys
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

logger = logging.getLogger(__name__)
_UNEXPECTED_ERROR_CHARS = 2000


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
        "output_limited": run.log_truncated,
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
        "output_limited": result.output_limited or run.log_truncated,
    }
    return _store_command_metadata(run, payload)


def _fallback_log(run: WorkspaceTaskRun, message: str) -> str:
    """
    Return the run's stored and buffered log plus ``message``, within the retained log budget.

    When everything does not fit, earlier output is truncated first so the failure line stays
    readable; the result never exceeds ``INSPECTOR_TASK_LOG_BYTES``.
    """
    limit = settings.INSPECTOR_TASK_LOG_BYTES
    stamp = timezone.now().strftime("%H:%M:%S")
    final = f"[{stamp}] {message}".encode("utf-8", errors="replace").decode("utf-8")
    buffered = getattr(run, "_log_buffer", None) or []
    earlier = "\n".join(part for part in [run.log, *buffered] if part)
    complete = f"{earlier}\n{final}" if earlier else final
    if len(complete.encode("utf-8", errors="replace")) <= limit:
        return complete
    marker_bytes = len(WorkspaceTaskRun.LOG_TRUNCATION_MARKER.encode("utf-8"))
    room = limit - len(final.encode("utf-8")) - 1
    if room < marker_bytes:
        # Not even the marker fits in front of the failure line: cut the failure line itself.
        return WorkspaceTaskRun.clamp_log(final, limit)
    return f"{WorkspaceTaskRun.clamp_log(earlier, room)}\n{final}"


def _record_unexpected_failure(run: WorkspaceTaskRun, error: BaseException | None) -> None:
    """
    Mark ``run`` failed after an error nothing else handled, without raising.

    The run's own logging path may be what failed, so the outcome is written with one direct
    update instead of ``append_log``/``save``. The caller's original error keeps propagating.
    """
    # Error text can be arbitrarily long (a database error may quote the statement it failed on).
    detail = f"{type(error).__name__}: {error}"[:_UNEXPECTED_ERROR_CHARS]
    message = f"Command execution failed unexpectedly: {detail}"
    try:
        log = _fallback_log(run, message)
        command = {
            "raw": run.preset.command,
            "workspace_path": run.workspace.project_path,
            "error": message,
        }
        WorkspaceTaskRun.objects.filter(pk=run.pk, status=WorkspaceTaskRun.Status.RUNNING).update(
            status=WorkspaceTaskRun.Status.FAILED,
            completed_at=timezone.now(),
            progress=100,
            log=log,
            metadata={**(run.metadata or {}), "command": command},
        )
    except Exception:
        logger.exception("Unable to record failure of workspace task run %s", run.pk)


@task()
def execute_workspace_task(task_run_id: int) -> dict[str, Any]:
    """Execute a SAFE Django management command for the selected workspace."""

    run = WorkspaceTaskRun.objects.select_related("workspace", "preset").get(pk=task_run_id)
    if not run.mark_running():
        run.refresh_from_db()
        return (run.metadata or {}).get("command") or {"skipped": True, "status": run.status}

    # Every path that leaves a claimed run without a recorded outcome ends up in ``finally``:
    # the run is marked failed there and the original error continues to the task backend.
    payload: dict[str, Any] | None = None
    try:
        payload = _execute_claimed_run(run)
    finally:
        if payload is None:
            _record_unexpected_failure(run, sys.exception())
    return payload


def _execute_claimed_run(run: WorkspaceTaskRun) -> dict[str, Any]:
    command = (run.preset.command or "").strip()
    try:
        safe_prefix = validate_safe_command(command)
    except CommandExecutionError as exc:
        return _fail_run(run, str(exc))

    run.progress = 5
    run.save(update_fields=["progress"])
    run.append_log(f"Executing `{command}` inside {run.workspace.project_path}")
    run.flush_log_buffer()
    if run.log_truncated:
        # Output could not be recorded at all, and a silent command would never be stopped
        # by the output callback, so the command is not started.
        return _fail_run(
            run,
            "Output truncated: retained log byte budget exhausted. Command not started.",
            safe_prefix=safe_prefix,
        )

    def log_output(line: str) -> None:
        run.append_log(line)
        if run.log_truncated:
            # Raising from the callback makes the runner stop the command's process group.
            raise CommandExecutionError(
                "Output truncated: retained log byte budget exhausted. Command stopped."
            )

    try:
        result = run_command(
            command=command,
            workspace_path=run.workspace.project_path,
            timeout=settings.INSPECTOR_TASK_TIMEOUT,
            log_callback=log_output,
        )
    except CommandExecutionError as exc:
        return _fail_run(run, str(exc), safe_prefix=safe_prefix)

    run.progress = 95
    run.save(update_fields=["progress"])
    if result.output_limited:
        run.append_log("Command stopped because its output exceeded the byte budget.")
    elif result.timed_out:
        run.append_log("Command timed out before completion.")
    else:
        run.append_log(f"Command finished with exit code {result.exit_code}.")
    run.flush_log_buffer()

    # A run whose output or retained log was cut short never counts as successful.
    payload = _success_payload(run, result)
    run.mark_finished(
        success=(result.exit_code == 0 and not result.timed_out and not payload["output_limited"])
    )

    # Ensure the calling view gets deterministic data even inside transactions.
    transaction.on_commit(lambda: None)
    return payload
