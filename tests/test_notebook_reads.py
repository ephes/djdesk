from __future__ import annotations

import io
import json
import os
from tempfile import TemporaryDirectory
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse

from djdesk.inspector import data_lab
from djdesk.inspector.models import Workspace


class NotebookReadTests(TestCase):
    old = (
        b'{"cells":[{"cell_type":"markdown","source":"old-a"},'
        b'{"cell_type":"markdown","source":"old-b"}]}'
    )
    new = old.replace(b"old-", b"new-")

    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.enterContext(override_settings(INSPECTOR_DATA_LAB_ROOT=temporary.name))
        self.workspace = Workspace.objects.create(name="Reading", project_path="/reading")
        self.path = data_lab.workspace_data_lab_dir(self.workspace) / "edited.ipynb"
        self.path.write_bytes(self.old)
        self.url = reverse("inspector:data-lab-notebook", args=[self.workspace.slug, "edited"])

    def split_read(self, mutate, sizes):
        opened = io.open
        middle = self.old.index(b"old-b")

        def opening(file, mode="r", *args, **kwargs):
            handle = opened(file, mode, *args, **kwargs)
            if not isinstance(file, int) or "r" not in mode:
                return handle
            read = handle.read

            def reading(size=-1):
                sizes.append(size)
                prefix = read(middle)
                # Drop read-ahead so the second half sees the actual concurrent write.
                handle.seek(handle.tell())
                mutate()
                return prefix + read(-1 if size < 0 else max(0, size - middle))

            handle.read = reading
            return handle

        return mock.patch("io.open", side_effect=opening)

    def edit_in_place(self, content, *, restore_mtime=False):
        before = self.path.stat()
        with self.path.open("r+b") as handle:
            handle.write(content)
            handle.truncate()
            handle.flush()
        # Ensure deterministic timestamp differences even on coarse filesystems.
        mtime = before.st_mtime_ns if restore_mtime else before.st_mtime_ns + 1_000_000_000
        os.utime(self.path, ns=(before.st_atime_ns, mtime))
        self.assertEqual(self.path.stat().st_ino, before.st_ino)

    def test_repeated_inplace_edits_refuse_valid_mixed_json_before_decoding(self):
        for portable in (False, True):
            with self.subTest(portable=portable):
                self.path.write_bytes(self.old)
                sizes = []

                def mutate():
                    self.edit_in_place(self.new if len(sizes) % 2 else self.old)

                with (
                    mock.patch.object(
                        os, "supports_dir_fd", set() if portable else os.supports_dir_fd
                    ),
                    self.split_read(mutate, sizes),
                ):
                    with mock.patch.object(data_lab.json, "loads", wraps=json.loads) as decode:
                        with self.assertRaises(FileNotFoundError):
                            data_lab.load_notebook(self.workspace, "edited")
                    decode.assert_not_called()
                    self.assertLessEqual(len(sizes), 3)
                    sizes.clear()
                    response = self.client.get(self.url)
                self.assertEqual(response.status_code, 404)
                self.assertLessEqual(len(sizes), 3)
                self.assertTrue(all(0 < size <= len(self.old) + 1 for size in sizes))
                self.assertNotIn(b"old-a", response.content)
                self.assertNotIn(b"new-b", response.content)

    def test_one_inplace_edit_retries_and_renders_a_complete_generation(self):
        sizes = []

        def mutate_once():
            if len(sizes) == 1:
                self.edit_in_place(self.new)

        with self.split_read(mutate_once, sizes):
            response = self.client.get(self.url)
        self.assertContains(response, "new-a")
        self.assertContains(response, "new-b")
        self.assertNotContains(response, "old-a")
        self.assertEqual(len(sizes), 2)
        self.assertEqual(self.path.read_bytes(), self.new)

    def test_same_size_edits_with_restored_mtime_are_refused(self):
        sizes = []

        def mutate():
            self.edit_in_place(self.new if len(sizes) % 2 else self.old, restore_mtime=True)

        with self.split_read(mutate, sizes):
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 404)
        self.assertLessEqual(len(sizes), 3)

    def test_atomic_replacement_returns_one_complete_generation(self):
        sizes = []
        replacement = self.path.with_suffix(".tmp")
        replacement.write_bytes(self.new)

        def replace_once():
            if len(sizes) == 1:
                replacement.replace(self.path)

        with self.split_read(replace_once, sizes):
            notebook = data_lab.load_notebook(self.workspace, "edited")
        self.assertIn(notebook, (json.loads(self.old), json.loads(self.new)))
        self.assertEqual(self.path.read_bytes(), self.new)
        self.assertLessEqual(len(sizes), 3)

    def test_stable_unicode_notebook_renders_without_modifying_bytes(self):
        content = self.old.replace(b"old-a", "Grüße".encode())
        self.path.write_bytes(content)
        response = self.client.get(self.url)
        self.assertContains(response, "Grüße")
        self.assertContains(response, "old-b")
        self.assertEqual(self.path.read_bytes(), content)
