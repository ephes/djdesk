"""A WAL writer commits between actual workspace/history reads."""

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from django.db import connections, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import RequestFactory, override_settings

from djdesk.inspector.models import ScanJob, Workspace, WorkspaceTaskRun
from djdesk.inspector.services import workspace_status_payload
from djdesk.inspector.views import DashboardView


class SnapshotRouter:
    def db_for_read(self, model, **hints):
        return hints.get("instance")._state.db if hints.get("instance") else "snapshot_read"


class HistoryReplicaRouter(SnapshotRouter):
    def db_for_read(self, model, **hints):
        if model in {ScanJob, WorkspaceTaskRun}:
            return "snapshot_write"
        return super().db_for_read(model, **hints)


class WorkspaceSnapshotTests(TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        for alias in ("snapshot_read", "snapshot_write"):
            config = deepcopy(connections["default"].settings_dict)
            config.update(ENGINE="django.db.backends.sqlite3", NAME=Path(temporary.name) / "db")
            config["OPTIONS"] = {}
            connections.databases[alias] = config
            self.addCleanup(self.close_alias, alias)
        reader = connections["snapshot_read"]
        with reader.cursor() as cursor:
            cursor.execute("PRAGMA journal_mode=WAL")
        executor = MigrationExecutor(reader)
        executor.migrate(executor.loader.graph.leaf_nodes())
        self.workspace = Workspace.objects.using("snapshot_read").create(
            name="Synthetic",
            slug="synthetic-snapshot",
            project_path="/synthetic/snapshot",
            metadata={"insights": ["old"]},
        )
        self.scan = ScanJob.objects.using("snapshot_read").create(
            workspace=self.workspace, kind="schema", summary="old"
        )
        router = override_settings(DATABASE_ROUTERS=[SnapshotRouter()])
        router.enable()
        self.addCleanup(router.disable)

    @staticmethod
    def close_alias(alias):
        connections[alias].close()
        del connections[alias]
        del connections.databases[alias]

    def commit_new_generation(self):
        with transaction.atomic(using="snapshot_write"):
            Workspace.objects.using("snapshot_write").filter(pk=self.workspace.pk).update(
                metadata={"insights": ["new"]}
            )
            ScanJob.objects.using("snapshot_write").filter(pk=self.scan.pk).update(summary="new")

    def read_with_interleaved_commit(self, read):
        committed = False

        def interleave(execute, sql, params, many, context):
            nonlocal committed
            if not committed and 'FROM "inspector_scanjob"' in sql:
                committed = True
                self.commit_new_generation()
            return execute(sql, params, many, context)

        with connections["snapshot_read"].execute_wrapper(interleave):
            result = read()
        self.assertTrue(committed)
        return result

    def test_poll_reloads_stale_workspace_in_one_snapshot(self):
        payload = self.read_with_interleaved_commit(
            lambda: workspace_status_payload(self.workspace)
        )
        self.assertEqual(payload["insights"], [payload["scans"][0]["summary"]])
        self.assertEqual(payload["insights"], ["old"])
        self.assertEqual(workspace_status_payload(self.workspace)["insights"], ["new"])

    def test_dashboard_materializes_history_before_rendering(self):
        view = DashboardView()
        view.setup(RequestFactory().get("/", {"workspace": self.workspace.slug}))

        def read_context():
            context = view.get_context_data()
            # Templates consume these outside get_context_data.
            list(context["recent_scans"])
            list(context["recent_task_runs"])
            return context

        context = self.read_with_interleaved_commit(read_context)
        self.assertEqual(context["workspace"].insights, [list(context["recent_scans"])[0].summary])
        self.assertIsInstance(context["recent_scans"], list)
        self.assertIsInstance(context["recent_task_runs"], list)

    @override_settings(DATABASE_ROUTERS=[HistoryReplicaRouter()])
    def test_snapshot_keeps_history_on_its_connection_despite_replica_router(self):
        def refuse_replica(execute, sql, params, many, context):
            raise AssertionError("History escaped the snapshot connection")

        with connections["snapshot_write"].execute_wrapper(refuse_replica):
            self.assertEqual(workspace_status_payload(self.workspace)["scans"][0]["summary"], "old")
