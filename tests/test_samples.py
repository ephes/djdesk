import errno
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from django.test import SimpleTestCase

from djdesk.inspector import samples


class BundledSampleTests(SimpleTestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.projects = {
            slug: path for slug, path, _ in samples.ensure_sample_projects(Path(temporary.name))
        }

    def run_polls_command(self, *args):
        env = os.environ.copy()
        env.pop("DJANGO_SETTINGS_MODULE", None)
        result = subprocess.run(
            [sys.executable, "-B", "manage.py", *args],
            cwd=self.projects["django-polls"],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_bundled_urlconfs_compile(self):
        for project in samples.SAMPLE_PROJECTS:
            for relative in project.files:
                if Path(relative).name == "urls.py":
                    with self.subTest(sample=project.slug, module=relative):
                        path = self.projects[project.slug] / relative
                        compile(path.read_bytes(), str(path), "exec")

    def test_polls_manage_check(self):
        self.run_polls_command("check")


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
        self.assertEqual(
            [p for p in self.root.iterdir() if p.suffix != ".lock"], [self.destination]
        )

    def test_later_write_failure_preserves_original(self):
        open_file = os.open

        def fail_later(path, *args, **kwargs):
            if Path(path).name == "later.txt":
                raise OSError("synthetic disk full")
            return open_file(path, *args, **kwargs)

        with mock.patch.object(os, "open", side_effect=fail_later):
            with self.assertRaisesRegex(OSError, "synthetic disk full"):
                samples.ensure_sample_projects(self.root, force=True)
        self.assert_original()

    def test_publication_failure_rolls_back(self):
        rename = os.rename
        attempted = []

        def fail_publish(path, target, **kwargs):
            if path == "replacement":
                attempted.append(path)
                raise OSError("synthetic rename failure")
            return rename(path, target, **kwargs)

        with mock.patch.object(os, "rename", side_effect=fail_publish):
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
        self.assertEqual(
            [p for p in self.root.iterdir() if p.suffix != ".lock"], [self.destination]
        )

    def test_failed_rollback_retains_recovery_copy(self):
        rename = os.rename

        def fail_publish_and_restore(path, target, **kwargs):
            if path in {"replacement", "backup"}:
                raise OSError("synthetic publication and rollback failure")
            return rename(path, target, **kwargs)

        with mock.patch.object(os, "rename", side_effect=fail_publish_and_restore):
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

    def test_missing_native_lock_refuses_without_falling_back_to_stale_marker(self):
        import fcntl

        with mock.patch.object(fcntl, "flock", side_effect=OSError(errno.ENOSYS, "no flock")):
            with self.assertRaises(OSError):
                samples.ensure_sample_projects(self.root, force=True)
        self.assert_original()
