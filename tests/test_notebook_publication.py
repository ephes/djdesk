from __future__ import annotations

import errno
import io
import json
import logging
import os
import stat
import subprocess
import sys
import threading
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

    def test_reads_leave_missing_export_directories_uncreated(self):
        for root in (self.root / "missing", self.root):
            with self.subTest(root=root), override_settings(INSPECTOR_DATA_LAB_ROOT=root):
                root_existed = root.exists()
                directory = root / self.workspace.slug
                dashboard = self.client.get(
                    reverse("inspector:dashboard"), {"workspace": self.workspace.slug}
                )
                self.assertEqual(dashboard.status_code, 200)
                self.assertFalse(directory.exists())
                self.assertEqual(root.exists(), root_existed)
                status = self.client.get(
                    reverse("inspector:workspace-status", args=[self.workspace.slug])
                )
                self.assertEqual(status.status_code, 200)
                self.assertEqual(status.json()["data_lab"]["notebooks"], [])
                self.assertFalse(status.json()["data_lab"]["notebooks_incomplete"])
                self.assertFalse(directory.exists())
                self.assertEqual(root.exists(), root_existed)
                missing = self.client.get(
                    reverse("inspector:data-lab-notebook", args=[self.workspace.slug, "missing"])
                )
                self.assertEqual(missing.status_code, 404)
                self.assertFalse(directory.exists())
                self.assertEqual(root.exists(), root_existed)

                exported = self.client.post(
                    reverse("inspector:data-lab-export", args=[self.workspace.slug]),
                    {"template": "schema-audit"},
                )
                self.assertEqual(exported.status_code, 201)
                self.assertTrue((directory / "schema-audit.ipynb").is_file())

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

    def test_export_templates_have_unique_stable_nbformat_cell_ids(self):
        for template in ("schema-audit", "log-study"):
            with self.subTest(template=template):
                first = data_lab.export_notebook(self.workspace, template)
                second = data_lab.export_notebook(self.workspace, template)
                notebook = json.loads(first.read_bytes())
                self.assertEqual((notebook["nbformat"], notebook["nbformat_minor"]), (4, 5))
                ids = [cell["id"] for cell in notebook["cells"]]
                self.assertTrue(ids)
                self.assertEqual(len(ids), len(set(ids)))
                for cell_id in ids:
                    self.assertRegex(cell_id, r"\A[A-Za-z0-9_-]{1,64}\Z")
                self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_committed_export_receipt_survives_optional_refresh_failure(self):
        for failure in ("inventory", "serialization"):
            with self.subTest(failure=failure):
                options = (
                    {"side_effect": RuntimeError("Synthetic inventory error")}
                    if failure == "inventory"
                    else {"return_value": {"data_lab": {}, "unserializable": object()}}
                )
                with (
                    mock.patch("djdesk.inspector.views.workspace_status_payload", **options),
                    self.assertLogs("djdesk.inspector.views", level="ERROR"),
                ):
                    response = self.client.post(
                        reverse("inspector:data-lab-export", args=[self.workspace.slug]),
                        {"template": "schema-audit"},
                    )
                self.assertEqual(response.status_code, 201)
                receipt = response.json()["data_lab"]
                slug = "schema-audit" if failure == "inventory" else "schema-audit-2"
                self.assertEqual(receipt["selected_notebook"], slug)
                self.assertEqual(self.client.get(receipt["selected_notebook_url"]).status_code, 200)
                directory = data_lab.workspace_data_lab_dir(self.workspace)
                self.assertEqual(
                    len(list(directory.glob("*.ipynb"))), 1 if failure == "inventory" else 2
                )
                self.assertEqual(
                    json.loads((directory / f"{slug}.ipynb").read_text())["nbformat"], 4
                )

    def test_committed_export_receipt_survives_failure_in_log_handler(self):
        logger = logging.getLogger("djdesk.inspector.views")
        handler = logging.Handler()
        self.addCleanup(handler.close)
        with (
            mock.patch(
                "djdesk.inspector.views.workspace_status_payload",
                side_effect=RuntimeError("Synthetic inventory error"),
            ),
            mock.patch.object(logger, "handlers", [handler]),
            mock.patch.object(handler, "emit", side_effect=OSError("log sink failed")) as emit,
        ):
            response = self.client.post(
                reverse("inspector:data-lab-export", args=[self.workspace.slug]),
                {"template": "schema-audit"},
            )

        self.assertEqual(response.status_code, 201)
        receipt = response.json()["data_lab"]
        self.assertEqual(receipt["selected_notebook"], "schema-audit")
        directory = data_lab.workspace_data_lab_dir(self.workspace)
        self.assertEqual(list(directory.glob("*.ipynb")), [directory / "schema-audit.ipynb"])
        emit.assert_called_once()

    def test_committed_export_receipt_survives_staging_unlink_failure(self):
        unlink = os.unlink
        events = []
        directory = data_lab.workspace_data_lab_dir(self.workspace)
        synced = self.classify_syncs(events, ("root", self.root))

        def fail_retirement(name, *, dir_fd=None):
            if dir_fd is not None and name.startswith(".notebook-"):
                self.assertEqual(events, ["file-sync", "workspace-sync", "root-sync"])
                raise OSError("staging unlink failed")
            return unlink(name, dir_fd=dir_fd)

        with (
            mock.patch.object(os, "fsync", side_effect=synced),
            mock.patch.object(os, "unlink", side_effect=fail_retirement) as patched_unlink,
            mock.patch.object(os, "supports_dir_fd", os.supports_dir_fd | {patched_unlink}),
            self.assertLogs("djdesk.inspector.data_lab", level="WARNING") as logs,
        ):
            response = self.client.post(
                reverse("inspector:data-lab-export", args=[self.workspace.slug]),
                {"template": "schema-audit"},
            )

        self.assertEqual(response.status_code, 201)
        receipt = response.json()["data_lab"]
        self.assertEqual(receipt["selected_notebook"], "schema-audit")
        self.assertEqual(self.client.get(receipt["selected_notebook_url"]).status_code, 200)
        published = directory / "schema-audit.ipynb"
        self.assertEqual(list(directory.glob("*.ipynb")), [published])
        self.assertEqual([item["slug"] for item in receipt["notebooks"]], ["schema-audit"])
        [staging] = directory.glob(".notebook-*.tmp")
        self.assertTrue(staging.samefile(published))
        self.assertIn(str(staging), "\n".join(logs.output))

    def test_export_preserves_outcome_when_descriptor_close_fails(self):
        close = os.close
        events = []
        real_sync = os.fsync
        for failure in ("file", "workspace", None):
            with self.subTest(failure=failure):
                closed = []
                events.clear()

                def sync(fd):
                    event = ("file", "workspace", "root")[len(events)]
                    events.append(fd)
                    if event == failure:
                        raise OSError(f"{failure} sync failed")
                    real_sync(fd)

                def fail_close(fd):
                    close(fd)
                    if fd in events:
                        closed.append(fd)
                        raise OSError("descriptor close failed")

                with (
                    mock.patch.object(os, "fsync", side_effect=sync),
                    mock.patch.object(os, "close", side_effect=fail_close),
                    self.assertLogs("djdesk.inspector.data_lab", level="WARNING") as logs,
                ):
                    if failure == "file":
                        with self.assertRaisesRegex(OSError, "file sync failed"):
                            data_lab.export_notebook(self.workspace, "schema-audit")
                        self.assertEqual(list(self.root.rglob("*.ipynb")), [])
                    elif failure == "workspace":
                        with self.assertRaises(data_lab.NotebookDurabilityError) as error:
                            data_lab.export_notebook(self.workspace, "schema-audit")
                        self.assertEqual(str(error.exception.__cause__), "workspace sync failed")
                        self.assertTrue(error.exception.published_path.is_file())
                    else:
                        path = data_lab.export_notebook(self.workspace, "schema-audit")
                        self.assertEqual(path.name, "schema-audit-2.ipynb")
                self.assertCountEqual(closed, events)
                self.assertEqual(len(logs.records), len(closed))
                self.assertEqual(list(self.root.rglob(".notebook-*.tmp")), [])

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

    def test_publication_syncs_file_then_workspace_then_root_before_success(self):
        events = []
        sync = os.fsync
        link = os.link
        root = self.root.stat()

        def synced(fd):
            info = os.fstat(fd)
            if os.path.samestat(root, info):
                events.append("root-sync")
            elif stat.S_ISDIR(info.st_mode):
                events.append("workspace-sync")
            else:
                events.append("file-sync")
            sync(fd)

        def linked(*args, **kwargs):
            link(*args, **kwargs)
            events.append("link")

        with (
            mock.patch.object(os, "fsync", side_effect=synced),
            mock.patch.object(os, "link", side_effect=linked) as patched_link,
            mock.patch.object(os, "supports_dir_fd", os.supports_dir_fd | {patched_link}),
            mock.patch.object(
                os, "supports_follow_symlinks", os.supports_follow_symlinks | {patched_link}
            ),
        ):
            for _ in range(2):
                path = data_lab.export_notebook(self.workspace, "schema-audit")
                events.append("success")
                self.assertTrue(path.is_file())
        expected = ["file-sync", "link", "workspace-sync", "root-sync", "success"]
        self.assertEqual(events, expected * 2)

    def classify_syncs(self, events, *named):
        sync = os.fsync

        def synced(fd):
            info = os.fstat(fd)
            for name, path in named:
                if path.exists() and os.path.samestat(path.stat(), info):
                    events.append(f"{name}-sync")
                    break
            else:
                events.append("workspace-sync" if stat.S_ISDIR(info.st_mode) else "file-sync")
            sync(fd)

        return synced

    def test_first_export_syncs_entries_of_created_export_root_before_publication(self):
        self.addCleanup(data_lab._unsynced_entry_parents.clear)
        outer = self.root / "outer"
        root = outer / "exports"
        events = []
        synced = self.classify_syncs(events, ("base", self.root), ("outer", outer), ("root", root))
        with (
            override_settings(INSPECTOR_DATA_LAB_ROOT=root),
            mock.patch.object(os, "fsync", side_effect=synced),
        ):
            first = data_lab.export_notebook(self.workspace, "schema-audit")
            self.assertEqual(
                events, ["outer-sync", "base-sync", "file-sync", "workspace-sync", "root-sync"]
            )
            events.clear()
            second = data_lab.export_notebook(self.workspace, "schema-audit")
            self.assertEqual(events, ["file-sync", "workspace-sync", "root-sync"])
        self.assertTrue(first.is_file())
        self.assertTrue(second.is_file())

    def test_unconfirmed_export_root_entry_is_synced_by_next_export(self):
        self.addCleanup(data_lab._unsynced_entry_parents.clear)
        root = self.root / "exports"
        events = []
        synced = self.classify_syncs(events, ("base", self.root), ("root", root))
        fail = [True]

        def failing(fd):
            if fail[0] and os.path.samestat(self.root.stat(), os.fstat(fd)):
                fail[0] = False
                raise OSError("injected base sync failure")
            synced(fd)

        with (
            override_settings(INSPECTOR_DATA_LAB_ROOT=root),
            mock.patch.object(os, "fsync", side_effect=failing),
        ):
            with self.assertRaisesRegex(OSError, "injected base sync failure"):
                data_lab.export_notebook(self.workspace, "schema-audit")
            self.assertTrue(root.is_dir())
            self.assertFalse((root / self.workspace.slug).exists())
            path = data_lab.export_notebook(self.workspace, "schema-audit")
        self.assertEqual(events, ["base-sync", "file-sync", "workspace-sync", "root-sync"])
        self.assertEqual(path.name, "schema-audit.ipynb")
        self.assertEqual(data_lab._unsynced_entry_parents, [])

    def test_concurrent_export_waits_for_created_export_root_sync(self):
        self.addCleanup(data_lab._unsynced_entry_parents.clear)
        root = self.root / "exports"
        other = Workspace.objects.create(name="Other", project_path="/other")
        base = self.root.stat()
        sync = os.fsync
        entered = threading.Event()
        release = threading.Event()
        order = []

        def blocking(fd):
            if os.path.samestat(base, os.fstat(fd)):
                entered.set()
                self.assertTrue(release.wait(10))
                order.append("base-sync")
            sync(fd)

        def export(workspace):
            path = data_lab.export_notebook(workspace, "schema-audit")
            order.append(workspace.slug)
            return path

        with (
            override_settings(INSPECTOR_DATA_LAB_ROOT=root),
            mock.patch.object(os, "fsync", side_effect=blocking),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            first = pool.submit(export, self.workspace)
            self.assertTrue(entered.wait(10))
            second = pool.submit(export, other)
            with self.assertRaises(TimeoutError):
                second.result(1)
            release.set()
            paths = [first.result(10), second.result(10)]
        self.assertEqual(order[0], "base-sync")
        self.assertEqual(order.count("base-sync"), 1)
        self.assertTrue(all(path.is_file() for path in paths))

    def test_publication_requests_full_device_flush_where_supported(self):
        calls = []

        def full_fsync(fd, command, *args):
            calls.append(("dir" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file", command))
            return 0

        fake_fcntl = mock.Mock(F_FULLFSYNC=51, fcntl=mock.Mock(side_effect=full_fsync))
        with mock.patch.object(data_lab, "fcntl", fake_fcntl):
            path = data_lab.export_notebook(self.workspace, "schema-audit")
        self.assertEqual(calls, [("file", 51), ("dir", 51), ("dir", 51)])
        self.assertTrue(path.is_file())

    def test_unsupported_full_flush_falls_back_to_fsync_and_other_errors_refuse(self):
        for code in (errno.ENOTSUP, errno.EOPNOTSUPP, errno.ENOSYS):
            unsupported = mock.Mock(
                F_FULLFSYNC=51, fcntl=mock.Mock(side_effect=OSError(code, "unsupported"))
            )
            with mock.patch.object(data_lab, "fcntl", unsupported):
                self.assertTrue(data_lab.export_notebook(self.workspace, "schema-audit").is_file())
        failing = mock.Mock(F_FULLFSYNC=51, fcntl=mock.Mock(side_effect=OSError(errno.EIO, "io")))
        with mock.patch.object(data_lab, "fcntl", failing):
            with self.assertRaises(OSError):
                data_lab.export_notebook(self.workspace, "log-study")
        without = mock.Mock(spec=[])
        with mock.patch.object(data_lab, "fcntl", without):
            self.assertTrue(data_lab.export_notebook(self.workspace, "log-study").is_file())

    def test_directory_sync_failure_reports_visible_export_without_rollback(self):
        old = data_lab.export_notebook(self.workspace, "schema-audit")
        old.write_bytes(b"manual edit")
        sync = os.fsync

        def failed_directory_sync(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError("injected directory sync failure")
            sync(fd)

        with mock.patch.object(os, "fsync", side_effect=failed_directory_sync):
            response = self.client.post(
                reverse("inspector:data-lab-export", args=[self.workspace.slug]),
                {"template": "schema-audit"},
            )
        self.assertEqual(response.status_code, 500)
        error = response.json()["errors"]["template"][0]
        self.assertIn("already published", error)
        self.assertIn("schema-audit-2.ipynb", error)
        self.assertIn("durability", error)
        self.assertNotIn("then retry", error)
        self.assertEqual(old.read_bytes(), b"manual edit")
        new = old.with_name("schema-audit-2.ipynb")
        self.assertEqual(
            json.loads(new.read_text()), data_lab.build_notebook(self.workspace, "schema-audit")
        )
        self.assertEqual(set(old.parent.iterdir()), {old, new})

    def test_uncertain_export_response_survives_failure_in_log_handler(self):
        directory = data_lab.workspace_data_lab_dir(self.workspace)
        failure = data_lab.NotebookDurabilityError(directory / "schema-audit.ipynb")
        logger = logging.getLogger("djdesk.inspector.views")
        handler = logging.Handler()
        self.addCleanup(handler.close)
        with (
            mock.patch.object(data_lab, "export_notebook", side_effect=failure) as export,
            mock.patch.object(logger, "handlers", [handler]),
            mock.patch.object(handler, "emit", side_effect=OSError("log sink failed")) as emit,
        ):
            response = self.client.post(
                reverse("inspector:data-lab-export", args=[self.workspace.slug]),
                {"template": "schema-audit"},
            )

        self.assertEqual(response.status_code, 500)
        error = response.json()["errors"]["template"][0]
        self.assertIn("'schema-audit.ipynb' is already published", error)
        self.assertIn("durability could not be confirmed", error)
        self.assertIn("Inspect the export directory before exporting again.", error)
        export.assert_called_once_with(self.workspace, "schema-audit")
        emit.assert_called_once()

    def test_first_export_root_sync_failure_reports_visible_notebook(self):
        root = self.root.stat()
        sync = os.fsync

        def fail_root_sync(fd):
            if os.path.samestat(root, os.fstat(fd)):
                raise OSError("injected export root sync failure")
            sync(fd)

        with (
            mock.patch.object(os, "fsync", side_effect=fail_root_sync),
            self.assertLogs("djdesk.inspector.views", level="ERROR"),
        ):
            response = self.client.post(
                reverse("inspector:data-lab-export", args=[self.workspace.slug]),
                {"template": "schema-audit"},
            )
        self.assertEqual(response.status_code, 500)
        error = response.json()["errors"]["template"][0]
        self.assertIn("'schema-audit.ipynb' is already published", error)
        self.assertIn("durability", error)
        published = data_lab.workspace_data_lab_dir(self.workspace) / "schema-audit.ipynb"
        self.assertEqual(
            json.loads(published.read_text()),
            data_lab.build_notebook(self.workspace, "schema-audit"),
        )
        self.assertEqual(list(published.parent.iterdir()), [published])

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

    def test_publication_os_error_returns_json_error_when_log_handler_fails(self):
        logger = logging.getLogger("djdesk.inspector.views")
        handler = logging.Handler()
        self.addCleanup(handler.close)
        with (
            mock.patch.object(os, "fsync", side_effect=OSError("staging write failed")),
            mock.patch.object(logger, "handlers", [handler]),
            mock.patch.object(handler, "emit", side_effect=OSError("log sink failed")) as emit,
        ):
            response = self.client.post(
                reverse("inspector:data-lab-export", args=[self.workspace.slug]),
                {"template": "schema-audit"},
            )
        self.assertEqual(response.status_code, 500)
        self.assertIn("could not be published", response.json()["errors"]["template"][0])
        self.assertEqual(list(self.root.rglob("*.ipynb")), [])
        emit.assert_called_once()

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
        directory.mkdir()
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
        directory.mkdir()
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
