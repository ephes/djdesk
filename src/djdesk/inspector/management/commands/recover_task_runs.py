from functools import partial

from django.core.management.base import BaseCommand, CommandError
from django.db import DEFAULT_DB_ALIAS, connections, transaction
from django.utils import timezone

from djdesk.inspector.models import WorkspaceTaskRun

RECOVERABLE = (WorkspaceTaskRun.Status.REQUESTED, WorkspaceTaskRun.Status.RUNNING)


class Command(BaseCommand):
    help = (
        "Mark explicitly selected abandoned REQUESTED or RUNNING runs failed; "
        "never replay commands."
    )

    def add_arguments(self, parser):
        parser.add_argument("--database", default=DEFAULT_DB_ALIAS, choices=tuple(connections))
        parser.add_argument("--run-id", type=int, action="append", required=True)
        parser.add_argument("--confirm-workers-stopped", action="store_true")
        parser.add_argument("--reason", default="")

    def handle(self, *args, **options):
        ids = set(options["run_id"])
        database = options["database"]
        reason = options["reason"].strip()
        if not options["confirm_workers_stopped"]:
            raise CommandError(
                "Stop all owning workers and commands, then confirm workers stopped."
            )
        if not reason or len(reason) > 280:
            raise CommandError("Supply an abandonment reason of 1–280 characters.")
        if not 1 <= len(ids) <= 100 or any(pk <= 0 for pk in ids):
            raise CommandError("Select 1–100 positive run IDs.")
        with transaction.atomic(using=database):
            runs = list(WorkspaceTaskRun.objects.using(database).filter(pk__in=ids))
            if len(runs) != len(ids) or any(run.status not in RECOVERABLE for run in runs):
                raise CommandError(
                    "Every selected run must exist and be REQUESTED or RUNNING; nothing changed."
                )
            for run in runs:
                never_started = run.status == WorkspaceTaskRun.Status.REQUESTED
                metadata = {
                    **(run.metadata or {}),
                    "recovery": {"reason": reason, "workers_stopped": True},
                    "command": {
                        "interrupted": not never_started,
                        "never_started": never_started,
                        "error": "Operator confirmed abandoned request; command never started."
                        if never_started
                        else "Operator confirmed abandoned run.",
                    },
                }
                # A stale snapshot (claimed or settled meanwhile) changes nothing.
                changed = (
                    WorkspaceTaskRun.objects.using(database)
                    .filter(pk=run.pk, status=run.status, started_at=run.started_at)
                    .update(
                        status="failed",
                        completed_at=timezone.now(),
                        progress=100,
                        metadata=metadata,
                    )
                )
                if not changed:
                    raise CommandError("A selected run changed during recovery; nothing changed.")
            receipt = f"Recovered {len(runs)} abandoned run(s). No commands replayed."
            transaction.on_commit(partial(self.stdout.write, receipt), using=database)
