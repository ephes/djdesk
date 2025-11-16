from __future__ import annotations

from typing import Any

from django.db import transaction
from django_tasks import task

from .content import TASK_SIMULATIONS
from .models import WorkspaceTaskRun


@task()
def execute_workspace_task(task_run_id: int) -> dict[str, Any]:
    """Emulates long-running management commands for tutorial purposes."""

    run = WorkspaceTaskRun.objects.select_related("workspace", "preset").get(pk=task_run_id)

    simulation = TASK_SIMULATIONS.get(run.preset.key)
    if simulation is None:
        run.append_log("No simulation available for this preset.")
        run.mark_finished(success=False)
        return {"status": run.status}

    run.mark_running()
    for step in simulation["steps"]:
        run.progress = step["progress"]
        run.save(update_fields=["progress"])
        run.append_log(step["message"])

    metadata = run.metadata or {}
    metadata["simulation"] = {
        "summary": simulation.get("summary"),
        "result": simulation.get("payload", {}),
    }
    run.metadata = metadata
    run.save(update_fields=["metadata"])
    run.mark_finished(success=True)

    # Ensure the calling view gets deterministic data even inside transactions.
    transaction.on_commit(lambda: None)
    return metadata["simulation"]
