from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from djdesk.inspector.models import WorkspaceTaskRun


class Command(BaseCommand):
    help = "Mark explicitly selected abandoned RUNNING runs failed; never replay commands."

    def add_arguments(self, parser):
        parser.add_argument("--run-id", type=int, action="append", required=True)
        parser.add_argument("--confirm-workers-stopped", action="store_true")
        parser.add_argument("--reason", default="")

    def handle(self, *args, **options):
        ids = set(options["run_id"])
        reason = options["reason"].strip()
        if not options["confirm_workers_stopped"]:
            raise CommandError(
                "Stop all owning workers and commands, then confirm workers stopped."
            )
        if not reason or len(reason) > 280:
            raise CommandError("Supply an abandonment reason of 1–280 characters.")
        if not 1 <= len(ids) <= 100 or any(pk <= 0 for pk in ids):
            raise CommandError("Select 1–100 positive run IDs.")
        with transaction.atomic():
            runs = list(WorkspaceTaskRun.objects.filter(pk__in=ids))
            if len(runs) != len(ids) or any(run.status != "running" for run in runs):
                raise CommandError("Every selected run must exist and be RUNNING; nothing changed.")
            for run in runs:
                metadata = {
                    **(run.metadata or {}),
                    "recovery": {"reason": reason, "workers_stopped": True},
                    "command": {"interrupted": True, "error": "Operator confirmed abandoned run."},
                }
                changed = WorkspaceTaskRun.objects.filter(
                    pk=run.pk, status="running", started_at=run.started_at
                ).update(
                    status="failed", completed_at=timezone.now(), progress=100, metadata=metadata
                )
                if not changed:
                    raise CommandError("A selected run changed during recovery; nothing changed.")
        self.stdout.write(f"Recovered {len(runs)} interrupted run(s). No commands replayed.")
