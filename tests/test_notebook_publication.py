from __future__ import annotations

import io
import json
import os
import stat
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse

from djdesk.inspector import data_lab
from djdesk.inspector.models import Workspace


class NotebookPublicationTests(TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.enterContext(override_settings(INSPECTOR_DATA_LAB_ROOT=self.root))
        self.workspace = Workspace.objects.create(name="Publication", project_path="/publication")

    def observe_write(self, observer):
        open_file = io.open

        def opened(*args, **kwargs):
            handle = open_file(*args, **kwargs)
            mode = kwargs.get("mode", args[1] if len(args) > 1 else "r")
            if "b" in mode or ("w" not in mode and "x" not in mode):
                return handle
            write = handle.write

            def partial(content):
                middle = len(content) // 2
                write(content[:middle])
                handle.flush()
                observer()
                write(content[middle:])
                return len(content)

            handle.write = partial
            return handle

        return mock.patch("io.open", side_effect=opened)

    def test_only_complete_notebooks_are_visible_during_write(self):
        snapshots = []
        with self.observe_write(
            lambda: snapshots.append(data_lab.list_workspace_exports(self.workspace))
        ):
            path = data_lab.export_notebook(self.workspace, "schema-audit")
        self.assertEqual(snapshots, [[]])
        self.assertEqual(
            json.loads(path.read_text()), data_lab.build_notebook(self.workspace, "schema-audit")
        )
        self.assertEqual([p.name for p in path.parent.iterdir()], [path.name])
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_concurrent_exports_preserve_every_generation(self):
        with ThreadPoolExecutor(max_workers=6) as pool:
            paths = list(
                pool.map(lambda _: data_lab.export_notebook(self.workspace, "log-study"), range(12))
            )
        self.assertEqual(len(set(paths)), 12)
        self.assertEqual(
            {p.name for p in paths},
            {"log-study.ipynb"} | {f"log-study-{i}.ipynb" for i in range(2, 13)},
        )
        for path in paths:
            self.assertEqual(
                json.loads(path.read_text()), data_lab.build_notebook(self.workspace, "log-study")
            )

    def test_killed_writer_leaves_no_listed_partial_notebook(self):
        script = """
import io, os, sys
os.environ["DJANGO_SETTINGS_MODULE"] = "djdesk.settings.test"
import django
django.setup()
from django.conf import settings
from djdesk.inspector import data_lab
from djdesk.inspector.models import Workspace
settings.INSPECTOR_DATA_LAB_ROOT = sys.argv[1]
open_file = io.open
def interrupted(*args, **kwargs):
    handle = open_file(*args, **kwargs)
    mode = kwargs.get("mode", args[1] if len(args) > 1 else "r")
    if "w" in mode or "x" in mode:
        def write(content):
            handle.write = original
            handle.write(content[:20])
            handle.flush()
            os._exit(23)
        original = handle.write
        handle.write = write
    return handle
io.open = interrupted
data_lab.export_notebook(Workspace(name="Crash", slug="publication"), "schema-audit")
"""
        result = subprocess.run(
            [sys.executable, "-c", script, str(self.root)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 23, result.stderr)
        self.assertEqual(data_lab.list_workspace_exports(self.workspace), [])
        path = data_lab.export_notebook(self.workspace, "schema-audit")
        self.assertEqual(path.name, "schema-audit.ipynb")
        self.assertEqual(len(list(path.parent.glob(".notebook-*.tmp"))), 1)

    def test_publication_failure_cleans_staging_and_preserves_edits(self):
        path = data_lab.export_notebook(self.workspace, "schema-audit")
        path.write_bytes(b"edited")
        with mock.patch.object(
            data_lab.os, "link", side_effect=OSError("publication failed")
        ) as link:
            with (
                mock.patch.object(os, "supports_dir_fd", os.supports_dir_fd | {link}),
                mock.patch.object(
                    os, "supports_follow_symlinks", os.supports_follow_symlinks | {link}
                ),
            ):
                with self.assertRaisesRegex(OSError, "publication failed"):
                    data_lab.export_notebook(self.workspace, "schema-audit")
        self.assertEqual(path.read_bytes(), b"edited")
        self.assertEqual(list(path.parent.iterdir()), [path])

    def test_directory_swap_cannot_redirect_publication_or_cleanup(self):
        for fail in (False, True):
            with self.subTest(fail=fail), TemporaryDirectory() as outside_dir:
                directory = data_lab.workspace_data_lab_dir(self.workspace)
                moved = self.root / f"owned-{fail}"
                outside = Path(outside_dir)
                sentinel = outside / "schema-audit.ipynb"
                sentinel.write_bytes(b"outside edit")

                def swap():
                    directory.rename(moved)
                    directory.symlink_to(outside, target_is_directory=True)
                    if fail:
                        raise OSError("injected write failure")

                try:
                    with self.observe_write(swap):
                        if fail:
                            with self.assertRaisesRegex(OSError, "injected"):
                                data_lab.export_notebook(self.workspace, "schema-audit")
                        else:
                            data_lab.export_notebook(self.workspace, "schema-audit")
                    self.assertEqual(sentinel.read_bytes(), b"outside edit")
                    self.assertEqual(list(outside.iterdir()), [sentinel])
                    self.assertEqual(list(moved.glob("*.tmp")), [])
                    if not fail:
                        self.assertEqual(
                            json.loads((moved / sentinel.name).read_text()),
                            data_lab.build_notebook(self.workspace, "schema-audit"),
                        )
                finally:
                    directory.unlink()

    def test_unsupported_platform_refuses_export_before_creating_files(self):
        with mock.patch.object(os, "supports_dir_fd", set()):
            response = self.client.post(
                reverse("inspector:data-lab-export", args=[self.workspace.slug]),
                {"template": "schema-audit"},
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(list(self.root.rglob("*.ipynb")), [])

    def test_publication_os_error_returns_json_error_without_files(self):
        with mock.patch.object(os, "fsync", side_effect=OSError("staging write failed")):
            response = self.client.post(
                reverse("inspector:data-lab-export", args=[self.workspace.slug]),
                {"template": "schema-audit"},
            )
        self.assertEqual(response.status_code, 500)
        self.assertIn("could not be published", response.json()["errors"]["template"][0])
        self.assertEqual(list(self.root.rglob("*.ipynb")), [])
        self.assertEqual(list(self.root.rglob(".notebook-*.tmp")), [])

    def test_replaced_staging_inode_is_neither_published_nor_deleted(self):
        def replace():
            staging = next(self.root.rglob(".notebook-*.tmp"))
            staging.unlink()
            staging.write_bytes(b"replacement owned by another writer")

        with self.observe_write(replace):
            with self.assertRaisesRegex(ValueError, "staging file changed"):
                data_lab.export_notebook(self.workspace, "schema-audit")
        self.assertEqual(list(self.root.rglob("*.ipynb")), [])
        replacement = next(self.root.rglob(".notebook-*.tmp"))
        self.assertEqual(replacement.read_bytes(), b"replacement owned by another writer")

    def test_swap_before_directory_open_cannot_create_outside_files(self):
        directory = data_lab.workspace_data_lab_dir(self.workspace)
        with TemporaryDirectory() as outside:

            def swap(workspace):
                directory.rename(self.root / "original")
                directory.symlink_to(outside, target_is_directory=True)
                return directory

            with mock.patch.object(data_lab, "workspace_data_lab_dir", side_effect=swap):
                with self.assertRaises((ValueError, OSError)):
                    data_lab.export_notebook(self.workspace, "schema-audit")
            self.assertEqual(list(Path(outside).iterdir()), [])

    def test_invalid_workspace_directory_names_are_refused(self):
        for slug in (".", "..", "../outside", "nested/child"):
            with self.subTest(slug=slug):
                self.workspace.slug = slug
                with self.assertRaises(ValueError):
                    data_lab.export_notebook(self.workspace, "schema-audit")
        self.assertEqual(list(self.root.iterdir()), [])

    def test_unaddressable_names_never_break_dashboard_or_status(self):
        directory = data_lab.workspace_data_lab_dir(self.workspace)
        names = ["my notes.ipynb", "über.ipynb", "notes.v1.ipynb", ".ipynb"]
        for name in names + ["valid-notes_2.ipynb"]:
            (directory / name).write_bytes(b'{"cells": []}')
        response = self.client.get(
            reverse("inspector:dashboard"), {"workspace": self.workspace.slug}
        )
        self.assertEqual(response.status_code, 200)
        response = self.client.get(
            reverse("inspector:workspace-status", args=[self.workspace.slug])
        )
        self.assertEqual(response.status_code, 200)
        notebooks = response.json()["data_lab"]["notebooks"]
        self.assertEqual([entry["slug"] for entry in notebooks], ["valid-notes_2"])
        self.assertEqual(self.client.get(notebooks[0]["viewer_url"]).status_code, 200)
        for name in names:
            self.assertEqual((directory / name).read_bytes(), b'{"cells": []}')
