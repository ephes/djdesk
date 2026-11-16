"""Workspace validation and collision recovery stay on the owning database."""

from copy import deepcopy
from unittest import TestCase, mock

from django.core.exceptions import ValidationError
from django.db import connections, transaction
from django.test import override_settings

from djdesk.inspector.models import Workspace


class WorkspaceRouter:
    def db_for_read(self, model, **hints):
        return "workspace_primary"

    def db_for_write(self, model, **hints):
        instance = hints.get("instance")
        return instance._state.db or "workspace_selected"


class WorkspaceDatabaseTests(TestCase):
    aliases = ("workspace_primary", "workspace_selected")

    def setUp(self):
        self.enterContext(override_settings(DATABASE_ROUTERS=[WorkspaceRouter()]))
        for alias in self.aliases:
            config = deepcopy(connections["default"].settings_dict)
            config.update(ENGINE="django.db.backends.sqlite3", NAME=":memory:", OPTIONS={})
            connections.databases[alias] = config
            self.addCleanup(self.close_alias, alias)
            with connections[alias].schema_editor() as editor:
                editor.create_model(Workspace)
            Workspace.objects.using(alias).bulk_create(
                [Workspace(pk=1, name="Shared", slug="shared", project_path=f"/{alias}")]
            )

    @staticmethod
    def close_alias(alias):
        connections[alias].close()
        del connections[alias]
        del connections.databases[alias]

    @staticmethod
    def refuse_query(execute, sql, params, many, context):
        raise AssertionError("Workspace operation escaped the owning database")

    def test_routed_and_explicit_writes_allocate_slugs_on_the_write_alias(self):
        primary, selected = self.aliases
        for using in (None, selected):
            with self.subTest(using=using):
                with connections[primary].execute_wrapper(self.refuse_query):
                    with connections["default"].execute_wrapper(self.refuse_query):
                        workspace = Workspace(name="Shared", project_path=f"/new/{using}")
                        workspace.save(using=using)
                self.assertEqual(workspace._state.db, selected)
                self.assertEqual(workspace.slug, "shared-2" if using is None else "shared-3")

    def test_clean_checks_local_duplicates_and_excludes_only_its_local_identity(self):
        primary, selected = self.aliases
        workspace = Workspace.objects.using(selected).get(pk=1)
        with connections[primary].execute_wrapper(self.refuse_query):
            workspace.clean()
            workspace.project_path = "  /workspace_selected  "
            with self.assertRaises(ValidationError):
                Workspace(name="Duplicate", project_path=workspace.project_path).clean()
            # The same path on a different database does not conflict.
            workspace.project_path = "/workspace_primary"
            workspace.clean()
            workspace.save(using=selected)
        self.assertEqual(
            Workspace.objects.using(primary).get(pk=1).project_path, "/workspace_primary"
        )

    def test_explicit_alias_overrides_router_and_preserves_other_database(self):
        primary, selected = self.aliases
        before = list(Workspace.objects.using(selected).values())
        with connections[selected].execute_wrapper(self.refuse_query):
            workspace = Workspace(name="Shared", project_path="/explicit-primary")
            workspace.save(using=primary)
        self.assertEqual(workspace._state.db, primary)
        self.assertEqual(workspace.slug, "shared-2")
        self.assertEqual(before, list(Workspace.objects.using(selected).values()))

    def test_collision_retry_uses_selected_savepoint_inside_outer_transaction(self):
        primary, selected = self.aliases
        for using in (None, selected):
            with self.subTest(using=using):
                with connections[primary].execute_wrapper(self.refuse_query):
                    with transaction.atomic(using=selected):
                        with mock.patch.object(
                            Workspace,
                            "_generate_unique_slug",
                            side_effect=["shared", f"new-{using}"],
                        ) as generate:
                            workspace = Workspace(name="New", project_path=f"/retry/{using}")
                            workspace.save(using=using)
                        self.assertEqual(generate.call_count, 2)
                        self.assertTrue(
                            Workspace.objects.using(selected).filter(pk=workspace.pk).exists()
                        )
