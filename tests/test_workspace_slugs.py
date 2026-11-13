from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest import mock

from django.db import IntegrityError, close_old_connections, connection
from django.test import SimpleTestCase, TestCase, TransactionTestCase

from djdesk.inspector.models import Workspace


def diagnostic_error(
    constraint="inspector_workspace_slug_key", table="inspector_workspace", code="23505"
):
    cause = Exception("postgres diagnostic")
    cause.sqlstate = code
    cause.diag = SimpleNamespace(constraint_name=constraint, table_name=table)
    error = IntegrityError("duplicate key")
    error.__cause__ = cause
    return error


class SlugDiagnosticTests(SimpleTestCase):
    def test_postgres_admits_only_workspace_slug_unique_violation(self):
        workspace = Workspace()
        self.assertTrue(workspace._is_slug_integrity_error(diagnostic_error()))
        for kwargs in (
            {"constraint": "inspector_workspace_unique_project_path"},
            {"table": "another_table"},
            {"code": "23503"},
        ):
            with self.subTest(kwargs=kwargs):
                self.assertFalse(workspace._is_slug_integrity_error(diagnostic_error(**kwargs)))

    def test_sqlite_admits_only_exact_workspace_slug_column(self):
        for column, expected in (
            ("inspector_workspace.slug", True),
            ("another_table.slug", False),
            ("inspector_workspace.project_path", False),
            ("inspector_workspace.slug, inspector_workspace.name", False),
        ):
            cause = sqlite3.IntegrityError(f"UNIQUE constraint failed: {column}")
            cause.sqlite_errorcode = sqlite3.SQLITE_CONSTRAINT_UNIQUE
            error = IntegrityError(str(cause))
            error.__cause__ = cause
            self.assertEqual(Workspace()._is_slug_integrity_error(error), expected)
        self.assertFalse(Workspace()._is_slug_integrity_error(IntegrityError(".slug")))

    def test_legacy_psycopg_pgcode_is_supported(self):
        error = diagnostic_error()
        error.__cause__.pgcode = error.__cause__.sqlstate
        del error.__cause__.sqlstate
        self.assertTrue(Workspace()._is_slug_integrity_error(error))


class PostgresSlugCollisionTests(TransactionTestCase):
    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest(
                "Run with djdesk.settings.test_postgresql for PostgreSQL collision coverage."
            )

    def test_concurrent_equal_names_get_distinct_slugs(self):
        barrier = threading.Barrier(2)
        generate = Workspace._generate_unique_slug
        seen = threading.local()

        def generate_together(workspace):
            slug = generate(workspace)
            if not getattr(seen, "waited", False):
                seen.waited = True
                barrier.wait(timeout=10)
            return slug

        def create(index):
            close_old_connections()
            try:
                workspace = Workspace.objects.create(
                    name="Concurrent Slug", project_path=f"/concurrent/{index}"
                )
                return workspace.slug
            finally:
                close_old_connections()

        with mock.patch.object(Workspace, "_generate_unique_slug", generate_together):
            with ThreadPoolExecutor(max_workers=2) as pool:
                slugs = list(pool.map(create, range(2)))
        self.assertEqual(set(slugs), {"concurrent-slug", "concurrent-slug-2"})


class WorkspaceConstraintTests(TestCase):
    def test_project_path_violation_is_not_retried(self):
        Workspace.objects.create(name="First", project_path="/duplicate")
        with mock.patch.object(
            Workspace, "_generate_unique_slug", return_value="distinct"
        ) as generate:
            with self.assertRaises(IntegrityError):
                Workspace.objects.create(name="Second", project_path="/duplicate")
        generate.assert_called_once()

    def test_slug_retries_are_bounded(self):
        Workspace.objects.create(name="Existing", slug="occupied", project_path="/existing")
        with mock.patch.object(
            Workspace, "_generate_unique_slug", return_value="occupied"
        ) as generate:
            with self.assertRaises(IntegrityError):
                Workspace.objects.create(name="Second", project_path="/second")
        self.assertEqual(generate.call_count, 5)
