from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from django.test import SimpleTestCase

from djdesk.inspector import samples


class SampleRefreshTests(SimpleTestCase):
    def test_generated_settings_are_valid_python(self):
        content = samples._render_settings("fixture", slug="fixture", extra_apps=["polls"])
        compile(content, "fixture/settings.py", "exec")

    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.destination = self.root / "fixture"
        self.destination.mkdir()
        (self.destination / "first.txt").write_bytes(b"original")
        (self.destination / "tutorial.txt").write_bytes(b"local state")
        project = samples.SampleProject(
            "fixture",
            "Fixture",
            "Synthetic sample",
            "fixture",
            {"first.txt": "replacement", "nested/later.txt": "later"},
        )
        patcher = mock.patch.object(samples, "SAMPLE_PROJECTS", [project])
        patcher.start()
        self.addCleanup(patcher.stop)

    def assert_original(self):
        self.assertEqual((self.destination / "first.txt").read_bytes(), b"original")
        self.assertEqual((self.destination / "tutorial.txt").read_bytes(), b"local state")
        self.assertEqual(list(self.root.iterdir()), [self.destination])

    def test_later_write_failure_preserves_original(self):
        write_text = Path.write_text

        def fail_later(path, *args, **kwargs):
            if path.name == "later.txt":
                raise OSError("synthetic disk full")
            return write_text(path, *args, **kwargs)

        with mock.patch.object(Path, "write_text", autospec=True, side_effect=fail_later):
            with self.assertRaisesRegex(OSError, "synthetic disk full"):
                samples.ensure_sample_projects(self.root, force=True)
        self.assert_original()

    def test_publication_failure_rolls_back(self):
        rename = Path.rename
        attempted = []

        def fail_publish(path, target):
            if path.name == "replacement":
                attempted.append(path)
                raise OSError("synthetic rename failure")
            return rename(path, target)

        with mock.patch.object(Path, "rename", autospec=True, side_effect=fail_publish):
            with self.assertRaisesRegex(OSError, "synthetic rename failure"):
                samples.ensure_sample_projects(self.root, force=True)
        self.assertEqual(len(attempted), 1)
        self.assert_original()

    def test_success_replaces_entire_tree(self):
        self.assertEqual(
            samples.ensure_sample_projects(self.root, force=True),
            [("fixture", self.destination, True)],
        )
        self.assertEqual((self.destination / "first.txt").read_bytes(), b"replacement\n")
        self.assertEqual((self.destination / "nested/later.txt").read_bytes(), b"later\n")
        self.assertFalse((self.destination / "tutorial.txt").exists())
        self.assertEqual(list(self.root.iterdir()), [self.destination])

    def test_failed_rollback_retains_recovery_copy(self):
        rename = Path.rename

        def fail_publish_and_restore(path, target):
            if path.name in {"replacement", "backup"}:
                raise OSError("synthetic publication and rollback failure")
            return rename(path, target)

        with mock.patch.object(Path, "rename", autospec=True, side_effect=fail_publish_and_restore):
            with self.assertRaisesRegex(OSError, "rollback failure"):
                samples.ensure_sample_projects(self.root, force=True)
        backups = list(self.root.glob(".fixture-*/backup"))
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / "first.txt").read_bytes(), b"original")
        self.assertEqual((backups[0] / "tutorial.txt").read_bytes(), b"local state")

    def test_without_force_preserves_original(self):
        self.assertEqual(
            samples.ensure_sample_projects(self.root), [("fixture", self.destination, False)]
        )
        self.assert_original()
