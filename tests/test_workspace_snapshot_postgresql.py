"""PostgreSQL workspace snapshots run at repeatable read (djdesk.settings.test_postgresql)."""

from django.db import NotSupportedError, connection, transaction
from django.test import TransactionTestCase

from djdesk.inspector.models import ScanJob, Workspace
from djdesk.inspector.services import workspace_snapshot


class PostgresWorkspaceSnapshotTests(TransactionTestCase):
    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest("Run with djdesk.settings.test_postgresql for PostgreSQL snapshots.")
        self.workspace = Workspace.objects.create(
            name="Synthetic", slug="synthetic-pg-snapshot", project_path="/synthetic/pg-snapshot"
        )
        ScanJob.objects.create(workspace=self.workspace, kind="schema", summary="synthetic")

    def isolation(self):
        with connection.cursor() as cursor:
            cursor.execute("SHOW transaction_isolation")
            return cursor.fetchone()[0]

    def test_fresh_snapshot_opens_a_repeatable_read_transaction(self):
        seen = []
        original = Workspace.objects.using

        def spy(alias):
            seen.append(self.isolation())
            return original(alias)

        Workspace.objects.using = spy
        try:
            current, scans, tasks = workspace_snapshot(self.workspace)
        finally:
            del Workspace.objects.using
        self.assertEqual(seen, ["repeatable read"])
        self.assertEqual(current.pk, self.workspace.pk)
        self.assertEqual([scan.summary for scan in scans], ["synthetic"])
        self.assertEqual(tasks, [])

    def test_nested_snapshot_refuses_a_read_committed_outer_transaction(self):
        with transaction.atomic():
            self.assertEqual(self.isolation(), "read committed")
            with self.assertRaises(NotSupportedError):
                workspace_snapshot(self.workspace)

    def test_nested_snapshot_accepts_a_repeatable_read_outer_transaction(self):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            current, _, _ = workspace_snapshot(self.workspace)
        self.assertEqual(current.pk, self.workspace.pk)
