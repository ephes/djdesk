"""Historical migrations always run against disposable, explicitly selected databases."""

from copy import deepcopy
from importlib import import_module, reload
from unittest import TestCase, mock

from django.db import connections
from django.db.migrations.exceptions import IrreversibleError
from django.db.migrations.executor import MigrationExecutor
from django.test import override_settings
from django.test.utils import CaptureQueriesContext


class HistoricalRouter:
    def db_for_read(self, model, **hints):
        if hints.get("instance"):
            return hints["instance"]._state.db
        return "migration_primary" if model._meta.app_label == "inspector" else None

    db_for_write = db_for_read


class ContentMigrationTests(TestCase):
    aliases = ("migration_primary", "migration_selected")

    def setUp(self):
        for alias in self.aliases:
            config = deepcopy(connections["default"].settings_dict)
            config.update(ENGINE="django.db.backends.sqlite3", NAME=":memory:")
            connections.databases[alias] = config
            self.addCleanup(self.close_alias, alias)
            self.migrate(alias, "0001_initial")
        self.router = override_settings(DATABASE_ROUTERS=[HistoricalRouter()])
        self.router.enable()
        self.addCleanup(self.router.disable)

    def close_alias(self, alias):
        connections[alias].close()
        del connections[alias]
        del connections.databases[alias]

    def migrate(self, alias, name):
        executor = MigrationExecutor(connections[alias])
        target = [("inspector", name)]
        executor.migrate(target)
        return executor.loader.project_state(target).apps

    def snapshot(self, alias, apps):
        return {
            model.__name__: list(model.objects.using(alias).order_by("pk").values())
            for model in apps.get_app_config("inspector").get_models()
        }

    def workspace(self, apps, alias, **overrides):
        values = {"name": "Synthetic", "slug": "synthetic", "project_path": "/synthetic"}
        values.update(overrides)
        return apps.get_model("inspector", "Workspace").objects.using(alias).create(**values)

    def test_seed_uses_selected_database(self):
        apps = self.migrate(self.aliases[0], "0001_initial")
        for alias in self.aliases:
            self.workspace(apps, alias)
        before = self.snapshot(self.aliases[0], apps)
        self.migrate(self.aliases[1], "0002_seed_content")
        self.assertEqual(self.snapshot(self.aliases[0], apps), before)
        selected = self.snapshot(self.aliases[1], apps)
        self.assertEqual(len(selected["Workspace"]), 2)
        self.assertEqual(len(selected["TaskPreset"]), 6)
        self.assertEqual(len(selected["DocLink"]), 6)
        self.assertEqual(len(selected["ScanJob"]), 3)
        self.assertEqual(len(selected["WorkspaceTaskRun"]), 2)

    def test_path_forward_and_reverse_use_selected_database(self):
        self.migrate(self.aliases[0], "0003_doclink_pane_target")
        apps = self.migrate(self.aliases[1], "0003_doclink_pane_target")
        for alias in self.aliases:
            first = self.workspace(apps, alias, project_path=" /synthetic/shared ")
            second = self.workspace(
                apps, alias, slug="synthetic-alias", project_path="/synthetic/shared"
            )
            apps.get_model("inspector", "ScanJob").objects.using(alias).create(
                workspace=second, kind="schema"
            )
            preset = (
                apps.get_model("inspector", "TaskPreset")
                .objects.using(alias)
                .create(key="synthetic-preset")
            )
            apps.get_model("inspector", "WorkspaceTaskRun").objects.using(alias).create(
                workspace=second, preset=preset
            )
        before = self.snapshot(self.aliases[0], apps)
        self.migrate(self.aliases[1], "0004_workspace_path_guards")
        self.assertEqual(self.snapshot(self.aliases[0], apps), before)
        workspace = apps.get_model("inspector", "Workspace").objects.using(self.aliases[1])
        self.assertFalse(workspace.filter(pk=second.pk).exists())
        self.assertEqual(workspace.get(pk=first.pk).project_path, "/synthetic/shared")
        self.assertEqual(workspace.get(pk=first.pk).scans.count(), 1)
        self.assertEqual(workspace.get(pk=first.pk).task_runs.count(), 1)
        normalized = self.snapshot(self.aliases[1], apps)
        self.migrate(self.aliases[1], "0003_doclink_pane_target")
        self.assertEqual(self.snapshot(self.aliases[0], apps), before)
        self.assertEqual(self.snapshot(self.aliases[1], apps), normalized)

    def test_seed_reverse_refuses_before_deleting_user_work(self):
        alias = self.aliases[1]
        apps = self.migrate(alias, "0002_seed_content")
        workspace = self.workspace(apps, alias, metadata={"custom": [1, 2]})
        preset = (
            apps.get_model("inspector", "TaskPreset")
            .objects.using(alias)
            .create(key="custom-preset")
        )
        apps.get_model("inspector", "WorkspaceTaskRun").objects.using(alias).create(
            workspace=workspace, preset=preset, log="Synthetic user history"
        )
        apps.get_model("inspector", "Workspace").objects.using(alias).exclude(
            pk=workspace.pk
        ).update(description="Customized seed")
        before = self.snapshot(alias, apps)
        with CaptureQueriesContext(connections[alias]) as queries:
            with self.assertRaises(IrreversibleError):
                self.migrate(alias, "0001_initial")
        self.assertEqual(self.snapshot(alias, apps), before)
        self.assertFalse(any(q["sql"].lstrip().upper().startswith("DELETE") for q in queries))

    def test_seed_is_independent_of_runtime_content_and_assets(self):
        from djdesk.inspector import assets, content

        with (
            override_settings(INSPECTOR_SAMPLE_ROOT="/synthetic/frozen-samples"),
            mock.patch.object(content, "TASK_PRESETS", []),
            mock.patch.object(content, "DOC_LINKS", []),
            mock.patch.object(content, "WORKSPACE_FIXTURES", []),
            mock.patch.object(assets, "sample_project_path", side_effect=AssertionError),
        ):
            frozen = import_module("djdesk.inspector.migrations._0002_seed_data")
            # Re-evaluate all frozen inputs under changed runtime content/settings.
            reload(frozen)
            module = import_module("djdesk.inspector.migrations.0002_seed_content")
            # Reload catches imports at module scope, as on a fresh installation.
            reload(module)
            apps = self.migrate(self.aliases[1], "0002_seed_content")
        reload(frozen)
        seeded = self.snapshot(self.aliases[1], apps)
        self.assertEqual(len(seeded["TaskPreset"]), 6)
        self.assertEqual(len(seeded["DocLink"]), 6)
        self.assertEqual(len(seeded["Workspace"]), 1)
        self.assertEqual(seeded["Workspace"][0]["name"], "Atlas Telemetry Studio")
        self.assertEqual(seeded["Workspace"][0]["python_version"], "3.14.0")
        self.assertEqual(seeded["Workspace"][0]["project_path"], "/synthetic/frozen-samples/djdesk")
        self.assertEqual(
            seeded["Workspace"][0]["metadata"]["status"]["project"],
            "/synthetic/frozen-samples/djdesk",
        )

    def test_pane_targets_are_frozen_and_use_selected_database(self):
        from djdesk.inspector import content

        apps = self.migrate(self.aliases[0], "0003_doclink_pane_target")
        self.migrate(self.aliases[1], "0002_seed_content")
        model = apps.get_model("inspector", "DocLink")
        model.objects.using(self.aliases[0]).update(pane_target="customized")
        before = self.snapshot(self.aliases[0], apps)
        with mock.patch.object(content, "DOC_LINKS", []):
            reload(import_module("djdesk.inspector.migrations.0003_doclink_pane_target"))
            self.migrate(self.aliases[1], "0003_doclink_pane_target")
        self.assertEqual(self.snapshot(self.aliases[0], apps), before)
        self.assertEqual(
            list(
                model.objects.using(self.aliases[1])
                .order_by("slug")
                .values_list("pane_target", flat=True)
            ),
            ["hello", "data-lab", "native", "packaging", "schema", "tasks"],
        )

    def assert_preflight_refusal(self, apps, expected):
        alias = self.aliases[1]
        before = self.snapshot(alias, apps)
        with CaptureQueriesContext(connections[alias]) as queries:
            with self.assertRaisesRegex(RuntimeError, expected):
                self.migrate(alias, "0004_workspace_path_guards")
        self.assertEqual(self.snapshot(alias, apps), before)
        writes = [
            q["sql"]
            for q in queries
            if q["sql"].lstrip().upper().startswith(("UPDATE", "DELETE", "INSERT"))
        ]
        self.assertEqual(writes, [])

    def test_blank_paths_refuse_before_any_write_then_allow_repair(self):
        alias = self.aliases[1]
        apps = self.migrate(alias, "0003_doclink_pane_target")
        model = apps.get_model("inspector", "Workspace")
        first = self.workspace(apps, alias, project_path="")
        second = self.workspace(apps, alias, slug="blank-two", project_path=" \t\u2003\n")
        self.assert_preflight_refusal(apps, "Repair")
        model.objects.using(alias).filter(pk=first.pk).update(project_path="/synthetic/first")
        model.objects.using(alias).filter(pk=second.pk).update(project_path="/synthetic/second")
        self.migrate(alias, "0004_workspace_path_guards")
        self.assertEqual(model.objects.using(alias).count(), 3)

    def test_conflicting_user_fields_refuse_before_any_write(self):
        alias = self.aliases[1]
        apps = self.migrate(alias, "0003_doclink_pane_target")
        model = apps.get_model("inspector", "Workspace")
        self.workspace(apps, alias, project_path=" /synthetic/shared ")
        second = self.workspace(apps, alias, slug="alias", project_path="/synthetic/shared")
        for field, conflict, original in [
            ("description", "Synthetic conflicting description", ""),
            ("metadata", {"synthetic": {"value": 2}}, {}),
            ("docs_url", "https://example.invalid/synthetic", ""),
            ("name", "Another synthetic name", "Synthetic"),
            ("python_version", "3.15", "3.14"),
            ("manage_py_detected", True, False),
            ("last_scan_at", "2026-01-01T00:00:00Z", None),
        ]:
            with self.subTest(field=field):
                model.objects.using(alias).filter(pk=second.pk).update(**{field: conflict})
                self.assert_preflight_refusal(apps, "conflicting")
                model.objects.using(alias).filter(pk=second.pk).update(**{field: original})

    def test_conflict_on_late_page_still_prevents_all_writes(self):
        alias = self.aliases[1]
        apps = self.migrate(alias, "0003_doclink_pane_target")
        model = apps.get_model("inspector", "Workspace")
        model.objects.using(alias).bulk_create(
            [
                model(name="Synthetic", slug=f"early-{i}", project_path=f" /early/{i} ")
                for i in range(30)
            ]
        )
        self.workspace(apps, alias, project_path=" /z/synthetic ")
        self.workspace(apps, alias, slug="late", project_path="/z/synthetic", metadata={"x": 1})
        module = import_module("djdesk.inspector.migrations.0004_workspace_path_guards")
        with mock.patch.object(module, "BATCH_SIZE", 7):
            self.assert_preflight_refusal(apps, "conflicting")

    def test_large_catalog_bounds_batches_queries_and_preserves_groups(self):
        alias = self.aliases[1]
        apps = self.migrate(alias, "0003_doclink_pane_target")
        model = apps.get_model("inspector", "Workspace")
        catalog_size, group_size, batch_size = 1200, 91, 37
        # Force small batches so the duplicate group spans several pages.
        module = import_module("djdesk.inspector.migrations.0004_workspace_path_guards")
        model.objects.using(alias).bulk_create(
            [
                model(name="Synthetic", slug=f"synthetic-{i}", project_path=f" /synthetic/{i:04} ")
                for i in range(catalog_size)
            ]
        )
        rows = list(model.objects.using(alias).filter(name="Synthetic").order_by("pk"))
        canonical = rows[0]
        # A whole group larger than a batch, including Python's Unicode whitespace.
        for row in rows[1:group_size]:
            model.objects.using(alias).filter(pk=row.pk).update(
                project_path="\u2003\t/synthetic/0000\n"
            )
        preset = apps.get_model("inspector", "TaskPreset").objects.using(alias).first()
        scans = apps.get_model("inspector", "ScanJob")
        runs = apps.get_model("inspector", "WorkspaceTaskRun")
        for row in rows[:group_size]:
            scans.objects.using(alias).create(workspace_id=row.pk, kind="schema", details={"x": 1})
            runs.objects.using(alias).create(
                workspace_id=row.pk, preset_id=preset.pk, log="synthetic"
            )
        children_before = {
            child.__name__: list(child.objects.using(alias).order_by("pk").values())
            for child in (scans, runs)
        }
        batch_lengths = []
        original_batches = module._path_batches

        def observed_batches(*args):
            for batch in original_batches(*args):
                batch_lengths.append(len(batch))
                yield batch

        with (
            mock.patch.object(module, "BATCH_SIZE", batch_size),
            mock.patch.object(module, "_path_batches", side_effect=observed_batches),
            mock.patch.object(model, "from_db", wraps=model.from_db) as hydrated,
            CaptureQueriesContext(connections[alias]) as queries,
        ):
            self.migrate(alias, "0004_workspace_path_guards")
        self.assertTrue(batch_lengths)
        self.assertLessEqual(max(batch_lengths), batch_size)
        # Reads use projections; at most the bounded deleted rows are hydrated.
        self.assertLessEqual(hydrated.call_count, group_size - 1)
        # Two read passes plus bounded writes per batch, rather than per row.
        self.assertLess(len(queries), 220)
        self.assertEqual(model.objects.using(alias).count(), catalog_size - group_size + 2)
        self.assertEqual(model.objects.using(alias).get(pk=canonical.pk).slug, canonical.slug)
        self.assertEqual(
            scans.objects.using(alias).filter(workspace_id=canonical.pk).count(), group_size
        )
        self.assertEqual(
            runs.objects.using(alias).filter(workspace_id=canonical.pk).count(), group_size
        )
        merged_ids = {row.pk for row in rows[:group_size]}
        for child in (scans, runs):
            expected = children_before[child.__name__]
            for record in expected:
                if record["workspace_id"] in merged_ids:
                    record["workspace_id"] = canonical.pk
            self.assertEqual(list(child.objects.using(alias).order_by("pk").values()), expected)
        self.assertTrue(
            all(
                path == path.strip()
                for path in model.objects.using(alias).values_list("project_path", flat=True)
            )
        )
