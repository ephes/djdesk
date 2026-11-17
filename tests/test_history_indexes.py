"""Populated upgrades and the actual bounded snapshot SQL on SQLite/PostgreSQL."""

import json
from copy import deepcopy
from unittest import TestCase

from django.db import connections
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from djdesk.inspector.models import Workspace
from djdesk.inspector.services import workspace_snapshot


class HistoryIndexTests(TestCase):
    alias = "history_upgrade"

    def setUp(self):
        config = deepcopy(connections["default"].settings_dict)
        if connections["default"].vendor == "postgresql":
            name = "test_djdesk_history_upgrade"
            with connections["default"].cursor() as cursor:
                cursor.execute(f'CREATE DATABASE "{name}"')
            self.addCleanup(self.drop_database, name)
            config["NAME"] = name
        else:
            config.update(ENGINE="django.db.backends.sqlite3", NAME=":memory:", OPTIONS={})
        connections.databases[self.alias] = config
        self.addCleanup(self.close_alias)
        self.connection = connections[self.alias]
        executor = MigrationExecutor(self.connection)
        target = [("inspector", "0005_alter_doclink_pane_target")]
        executor.migrate(target)
        apps = executor.loader.project_state(target).apps
        workspace = apps.get_model("inspector", "Workspace")
        self.workspaces = [
            workspace.objects.using(self.alias).create(
                name=f"Synthetic {i}", slug=f"history-{i}", project_path=f"/synthetic/{i}"
            )
            for i in range(2)
        ]
        preset = apps.get_model("inspector", "TaskPreset").objects.using(self.alias).first()
        self.expected = {}
        for model_name, extra in (
            ("ScanJob", {"kind": "schema"}),
            ("WorkspaceTaskRun", {"preset_id": preset.pk}),
        ):
            model = apps.get_model("inspector", model_name)
            model.objects.using(self.alias).bulk_create(
                [model(workspace_id=row.pk, **extra) for _ in range(600) for row in self.workspaces]
            )
            # All timestamps tie: the primary key must determine the bounded window.
            model.objects.using(self.alias).update(created_at=timezone.now())
            self.expected[model_name] = list(
                model.objects.using(self.alias)
                .filter(workspace_id=self.workspaces[0].pk)
                .order_by("-created_at", "-pk")
                .values_list("pk", flat=True)
            )
        executor = MigrationExecutor(self.connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        if self.connection.vendor == "postgresql":
            # Autovacuum would gather these statistics on a populated installation;
            # without them the planner sizes the fresh bulk-loaded tables as tiny.
            with self.connection.cursor() as cursor:
                cursor.execute(
                    "ANALYZE inspector_workspace, inspector_scanjob, "
                    "inspector_workspacetaskrun, inspector_taskpreset"
                )

    def close_alias(self):
        connections[self.alias].close()
        del connections[self.alias]
        del connections.databases[self.alias]

    def drop_database(self, name):
        with connections["default"].cursor() as cursor:
            cursor.execute(f'DROP DATABASE "{name}"')

    def test_populated_upgrade_preserves_windows_and_uses_ordered_indexes(self):
        workspace = Workspace.objects.using(self.alias).get(pk=self.workspaces[0].pk)
        for include_logs in (False, True):
            queries = []

            def capture(execute, sql, params, many, context):
                if sql.startswith("SELECT") and any(
                    f'FROM "inspector_{name}"' in sql for name in ("scanjob", "workspacetaskrun")
                ):
                    queries.append((sql, params))
                return execute(sql, params, many, context)

            with self.connection.execute_wrapper(capture):
                _, scans, tasks = workspace_snapshot(workspace, include_logs=include_logs)
            self.assertEqual([row.pk for row in scans], self.expected["ScanJob"][:4])
            self.assertEqual([row.pk for row in tasks], self.expected["WorkspaceTaskRun"][:5])
            self.assertEqual("details" in scans[0].get_deferred_fields(), not include_logs)
            self.assertEqual("log" in tasks[0].get_deferred_fields(), not include_logs)
            self.assertEqual(len(queries), 2)
            for (sql, params), index in zip(
                queries, ("scan_workspace_recent_idx", "task_workspace_recent_idx")
            ):
                with self.subTest(logs=include_logs, index=index):
                    with self.connection.cursor() as cursor:
                        if self.connection.vendor == "postgresql":
                            cursor.execute("EXPLAIN (FORMAT JSON) " + sql, params)
                            plan = json.dumps(cursor.fetchone()[0])
                            self.assertNotIn('"Node Type": "Sort"', plan)
                        else:
                            cursor.execute("EXPLAIN QUERY PLAN " + sql, params)
                            plan = str(cursor.fetchall())
                            self.assertNotIn("TEMP B-TREE", plan)
                    self.assertIn(index, plan)
