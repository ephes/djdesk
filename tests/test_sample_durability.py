"""Publication ordering and failure outcomes, using real temporary files."""

import os
import stat
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from django.test import SimpleTestCase

from djdesk.inspector import sample_publication, samples


class SampleDurabilityTests(SimpleTestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.destination = self.root / "fixture"
        self.destination.mkdir()
        (self.destination / "value.txt").write_text("old")
        self.project = samples.SampleProject(
            "fixture", "Fixture", "Synthetic", "fixture", {"nested/value.txt": "new"}
        )
        patcher = mock.patch.object(samples, "SAMPLE_PROJECTS", [self.project])
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_file_sync_failure_never_moves_old_generation(self):
        fsync = os.fsync
        calls = []

        def fail_file(fd):
            if stat.S_ISREG(os.fstat(fd).st_mode):
                calls.append(fd)
                raise OSError("file sync failed")
            return fsync(fd)

        with mock.patch.object(os, "fsync", side_effect=fail_file):
            with self.assertRaisesRegex(OSError, "file sync failed"):
                samples.ensure_sample_projects(self.root, force=True)
        self.assertTrue(calls)
        self.assertEqual((self.destination / "value.txt").read_text(), "old")

    def test_visible_swap_sync_failure_retains_backup_and_reports_uncertainty(self):
        fsync = os.fsync

        def fail_after_install(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode) and (self.destination / "nested").exists():
                raise OSError("directory sync failed")
            return fsync(fd)

        with mock.patch.object(os, "fsync", side_effect=fail_after_install):
            with self.assertRaisesRegex(OSError, "already installed.*durability.*uncertain"):
                samples.ensure_sample_projects(self.root, force=True)
        self.assertEqual((self.destination / "nested/value.txt").read_text(), "new\n")
        backup = next(self.root.glob(".fixture-*/backup"))
        self.assertEqual((backup / "value.txt").read_text(), "old")
        self.assertTrue((self.root / ".fixture.swap.json").exists())
        with self.assertWarns(RuntimeWarning):
            samples.ensure_sample_projects(self.root)
        self.assertEqual((backup / "value.txt").read_text(), "old")

    def test_files_and_directories_sync_before_backup_is_deleted(self):
        events = []
        fsync, rename, rmtree = os.fsync, Path.rename, sample_publication.shutil.rmtree

        def record_sync(fd):
            info = os.fstat(fd)
            events.append(("dir" if stat.S_ISDIR(info.st_mode) else "file", info.st_ino))
            return fsync(fd)

        def record_rename(path, target):
            events.append(("rename", path.name))
            return rename(path, target)

        def record_remove(path, *args, **kwargs):
            if (path / "backup").exists():
                events.append(("delete-backup", None))
            return rmtree(path, *args, **kwargs)

        with (
            mock.patch.object(os, "fsync", side_effect=record_sync),
            mock.patch.object(Path, "rename", autospec=True, side_effect=record_rename),
            mock.patch.object(sample_publication.shutil, "rmtree", side_effect=record_remove),
        ):
            samples.ensure_sample_projects(self.root, force=True)
        moved_old = events.index(("rename", "fixture"))
        installed = events.index(("rename", "replacement"))
        retired = events.index(("delete-backup", None))
        root_inode = self.root.stat().st_ino
        file_inode = (self.destination / "nested/value.txt").stat().st_ino
        nested_inode = (self.destination / "nested").stat().st_ino
        self.assertIn(("file", file_inode), events[:moved_old])
        self.assertIn(("dir", nested_inode), events[:moved_old])
        self.assertIn(("dir", root_inode), events[:moved_old])
        self.assertIn(("dir", root_inode), events[installed + 1 : retired])

    def test_each_directory_sync_failure_preserves_a_complete_generation(self):
        # Every directory barrier in a successful swap must fail safely on its own.
        calls = []
        fsync = os.fsync

        def count(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                calls.append(fd)
            return fsync(fd)

        with mock.patch.object(os, "fsync", side_effect=count):
            samples.ensure_sample_projects(self.root, force=True)
        for barrier in range(len(calls)):
            with self.subTest(barrier=barrier), TemporaryDirectory() as temporary:
                root = Path(temporary)
                destination = root / "fixture"
                destination.mkdir()
                (destination / "value.txt").write_text("old")
                seen = 0

                def fail_once(fd):
                    nonlocal seen
                    if stat.S_ISDIR(os.fstat(fd).st_mode):
                        seen += 1
                        if seen == barrier + 1:
                            raise OSError("injected directory barrier failure")
                    return fsync(fd)

                with mock.patch.object(os, "fsync", side_effect=fail_once):
                    with self.assertRaises(OSError):
                        samples.ensure_sample_projects(root, force=True)
                if (destination / "nested/value.txt").exists():
                    self.assertEqual((destination / "nested/value.txt").read_text(), "new\n")
                    self.assertEqual(
                        next(root.glob(".fixture-*/backup/value.txt")).read_text(), "old"
                    )
                else:
                    self.assertEqual((destination / "value.txt").read_text(), "old")

    def test_new_nested_sample_root_syncs_each_created_directory_entry(self):
        synced = set()
        fsync = os.fsync

        def record(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                synced.add(os.fstat(fd).st_ino)
            return fsync(fd)

        root = self.root / "new" / "nested" / "samples"
        with mock.patch.object(os, "fsync", side_effect=record):
            samples.ensure_sample_projects(root)
        for directory in (self.root, self.root / "new", root.parent, root):
            self.assertIn(directory.stat().st_ino, synced)
