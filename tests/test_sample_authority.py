"""Real namespace replacements at publication and recovery barriers."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from django.test import SimpleTestCase

from djdesk.inspector import sample_publication as publication
from djdesk.inspector import samples

PAUSED_PUBLISHER = """
import os, sys, time
from pathlib import Path
from djdesk.inspector.sample_publication import publish_sample
root, signals = Path(sys.argv[1]), Path(sys.argv[2])
rename = os.rename
def paused(source, target, **kwargs):
    if source == 'replacement':
        (signals / 'ready').touch()
        while not (signals / 'release').exists():
            time.sleep(.01)
    return rename(source, target, **kwargs)
os.rename = paused
publish_sample(root / 'fixture', {'value.txt': 'new'})
"""


class SampleAuthorityTests(SimpleTestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.new_namespace("samples")

    def new_namespace(self, label):
        self.root = self.base / label
        self.foreign = self.base / (label + "-foreign")
        for root in (self.root, self.foreign):
            (root / "fixture").mkdir(parents=True)
            (root / "fixture/value.txt").write_text("sentinel" if root == self.foreign else "old")
        self.destination = self.root / "fixture"

    def replace(self, path, symlink):
        saved = path.with_name(path.name + "-saved")
        path.rename(saved)
        if symlink:
            path.symlink_to(self.foreign, target_is_directory=True)
        else:
            path.mkdir()
            (path / "sentinel").write_text("foreign generation")
        return saved

    def test_root_replaced_before_intent_creation_preserves_both_trees(self):
        for symlink in (True, False):
            with self.subTest(symlink=symlink):
                self.new_namespace(f"root-{symlink}")
                open_file = os.open
                saved = None

                def replace_before_open(path, flags, *args, **kwargs):
                    nonlocal saved
                    if Path(path).name == ".fixture.swap.json" and flags & os.O_CREAT:
                        saved = self.replace(self.root, symlink)
                    return open_file(path, flags, *args, **kwargs)

                with mock.patch.object(os, "open", side_effect=replace_before_open):
                    with self.assertRaises(OSError):
                        publication.publish_sample(self.destination, {"value.txt": "new"})
                self.assertEqual((self.foreign / "fixture/value.txt").read_text(), "sentinel")
                self.assertFalse((self.foreign / ".fixture.swap.json").exists())
                self.assertEqual((saved / "fixture/value.txt").read_text(), "old")
                self.assertTrue(list(saved.glob(".fixture-*/replacement/value.txt")))
                if symlink:
                    self.root.unlink()
                else:
                    (self.root / "sentinel").unlink()
                    self.root.rmdir()
                saved.rename(self.root)

    def test_stage_replaced_after_intent_write_is_retained_without_foreign_mutation(self):
        for symlink in (True, False):
            with self.subTest(symlink=symlink):
                self.new_namespace(f"stage-{symlink}")
                dump = json.dump
                saved = None

                def replace_after_dump(intent, handle):
                    nonlocal saved
                    result = dump(intent, handle)
                    saved = self.replace(self.root / intent["stage"]["name"], symlink)
                    return result

                with mock.patch.object(json, "dump", side_effect=replace_after_dump):
                    with self.assertRaises(OSError):
                        publication.publish_sample(self.destination, {"value.txt": "new"})
                self.assertEqual((self.destination / "value.txt").read_text(), "old")
                self.assertEqual((self.foreign / "fixture/value.txt").read_text(), "sentinel")
                self.assertFalse((self.foreign / "backup").exists())
                self.assertEqual((saved / "replacement/value.txt").read_text(), "new\n")
                self.assertTrue((self.root / ".fixture.swap.json").exists())
                # Separate attempts need independent staging and journals.
                (self.root / ".fixture.swap.json").unlink()

    def test_recovery_parent_replacement_after_preflight_preserves_journal(self):
        for parent in ("root", "stage"):
            with self.subTest(parent=parent):
                self.new_namespace(f"recovery-{parent}")
                self.assert_recovery_parent_replacement(parent)

    def assert_recovery_parent_replacement(self, parent):
        stage = self.root / ".fixture-interrupted"
        stage.mkdir()
        (stage / "replacement").mkdir()
        (stage / "replacement/value.txt").write_text("new")
        self.destination.rename(stage / "backup")
        intent = {
            "version": 1,
            "root": publication._identity(self.root),
            "stage": {"name": stage.name, "identity": publication._identity(stage)},
            "old": publication._identity(stage / "backup"),
            "new": publication._identity(stage / "replacement"),
        }
        (self.root / ".fixture.swap.json").write_text(json.dumps(intent))
        # A pathname-based restore would move this unrelated tree into the foreign
        # public name. The initially checked backup belongs to the real root.
        (self.foreign / "fixture/value.txt").unlink()
        (self.foreign / "fixture").rmdir()
        foreign_backup = (
            self.foreign / stage.name / "backup" if parent == "root" else self.foreign / "backup"
        )
        foreign_backup.mkdir(parents=True)
        (foreign_backup / "sentinel").write_text("foreign backup")
        identity = publication._identity
        swapped = False

        def replace_after_identity(path, *args, **kwargs):
            nonlocal swapped
            result = identity(path, *args, **kwargs)
            if Path(path).name == "replacement" and not swapped:
                swapped = True
                self.replace(self.root if parent == "root" else stage, True)
            return result

        with mock.patch.object(publication, "_identity", side_effect=replace_after_identity):
            with self.assertRaisesRegex(OSError, "manual inspection"):
                publication.recover_sample(self.destination)
        self.assertFalse((self.foreign / "fixture").exists())
        self.assertEqual((foreign_backup / "sentinel").read_text(), "foreign backup")
        saved_root = (
            self.root.with_name(self.root.name + "-saved") if parent == "root" else self.root
        )
        saved_stage = (
            saved_root / stage.name if parent == "root" else stage.with_name(stage.name + "-saved")
        )
        self.assertTrue((saved_root / ".fixture.swap.json").exists())
        self.assertEqual((saved_stage / "backup/value.txt").read_text(), "old")

    def test_parent_replacements_at_rename_and_cleanup_boundaries(self):
        for boundary in ("fixture", "replacement", "cleanup"):
            for parent in ("root", "stage"):
                for symlink in (True, False):
                    label = f"{boundary}-{parent}-{symlink}"
                    with self.subTest(label=label):
                        self.new_namespace(label)
                        rename, remove_tree = os.rename, publication._remove_tree
                        saved = None
                        stage_name = None

                        def swap():
                            nonlocal saved, stage_name
                            stage = (
                                next(self.root.glob(".fixture-*/replacement")).parent
                                if boundary == "fixture"
                                else next(self.root.glob(".fixture-*"))
                            )
                            stage_name = stage.name
                            saved = self.replace(self.root if parent == "root" else stage, symlink)

                        def replace_before_rename(source, target, **kwargs):
                            if source == boundary:
                                swap()
                            return rename(source, target, **kwargs)

                        def replace_before_cleanup(*args, **kwargs):
                            if boundary == "cleanup":
                                swap()
                            return remove_tree(*args, **kwargs)

                        with (
                            mock.patch.object(os, "rename", side_effect=replace_before_rename),
                            mock.patch.object(
                                publication, "_remove_tree", side_effect=replace_before_cleanup
                            ),
                        ):
                            with self.assertRaises(OSError) as caught:
                                publication.publish_sample(self.destination, {"value.txt": "new"})
                        self.assertIsNotNone(saved)
                        self.assertTrue(
                            any("retained" in note.lower() for note in caught.exception.__notes__)
                        )
                        self.assertEqual(
                            (self.foreign / "fixture/value.txt").read_text(), "sentinel"
                        )
                        self.assertEqual(
                            sorted(p.name for p in self.foreign.iterdir()), ["fixture"]
                        )
                        retained_stage = saved / stage_name if parent == "root" else saved
                        self.assertEqual((retained_stage / "backup/value.txt").read_text(), "old")
                        if boundary != "cleanup":
                            owned_root = saved if parent == "root" else self.root
                            self.assertTrue((owned_root / ".fixture.swap.json").exists())

    def test_separate_process_cannot_redirect_install_after_parent_replacement(self):
        for parent in ("root", "stage"):
            with self.subTest(parent=parent):
                self.new_namespace(f"process-{parent}")
                signals = self.base / f"signals-{parent}"
                signals.mkdir()
                process = subprocess.Popen(
                    [sys.executable, "-c", PAUSED_PUBLISHER, str(self.root), str(signals)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                try:
                    deadline = time.monotonic() + 10
                    while not (signals / "ready").exists():
                        if process.poll() is not None or time.monotonic() > deadline:
                            self.fail("Publisher did not reach install barrier")
                        time.sleep(0.01)
                    stage = next(self.root.glob(".fixture-*"))
                    saved = self.replace(self.root if parent == "root" else stage, True)
                    (signals / "release").touch()
                    _, errors = process.communicate(timeout=10)
                    self.assertNotEqual(process.returncode, 0)
                    self.assertIn(b"paths may have moved", errors)
                    retained_stage = saved / stage.name if parent == "root" else saved
                    self.assertEqual((retained_stage / "backup/value.txt").read_text(), "old")
                    self.assertEqual((self.foreign / "fixture/value.txt").read_text(), "sentinel")
                    self.assertEqual(sorted(p.name for p in self.foreign.iterdir()), ["fixture"])
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.communicate(timeout=10)

    def test_cleanup_unlinks_backup_symlinks_without_following_them(self):
        (self.destination / "outside").symlink_to(self.foreign, target_is_directory=True)
        publication.publish_sample(self.destination, {"value.txt": "new"})
        self.assertEqual((self.foreign / "fixture/value.txt").read_text(), "sentinel")
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["fixture"])

    def test_replaced_backup_is_never_deleted_during_cleanup(self):
        open_file = os.open
        saved = None

        def swap_before_cleanup_open(path, flags, *args, **kwargs):
            nonlocal saved
            if path == "backup":
                stage = next(self.root.glob(".fixture-*"))
                saved = self.replace(stage / "backup", False)
            return open_file(path, flags, *args, **kwargs)

        with mock.patch.object(os, "open", side_effect=swap_before_cleanup_open):
            with self.assertRaisesRegex(OSError, "Cleanup generation changed"):
                publication.publish_sample(self.destination, {"value.txt": "new"})
        self.assertEqual((saved / "value.txt").read_text(), "old")
        self.assertEqual((saved.with_name("backup") / "sentinel").read_text(), "foreign generation")

    def test_root_owner_survives_recovery_to_publication_gap(self):
        recover = samples.recover_sample
        fixture = samples.SampleProject(
            "fixture", "Fixture", "Synthetic", "fixture", {"value.txt": "new"}
        )

        def replace_after_recovery(*args, **kwargs):
            result = recover(*args, **kwargs)
            self.root.rename(self.base / "samples-saved")
            self.foreign.rename(self.root)
            return result

        with (
            mock.patch.object(samples, "SAMPLE_PROJECTS", [fixture]),
            mock.patch.object(samples, "recover_sample", side_effect=replace_after_recovery),
        ):
            with self.assertRaises(OSError):
                samples.ensure_sample_projects(self.root, force=True)
        self.assertEqual((self.root / "fixture/value.txt").read_text(), "sentinel")
        self.assertEqual((self.base / "samples-saved/fixture/value.txt").read_text(), "old")

    def test_root_replaced_before_and_between_locks_never_locks_foreign_tree(self):
        projects = [
            samples.SampleProject(slug, slug, "Synthetic", slug, {"value.txt": "new"})
            for slug in ("fixture", "second")
        ]
        for swap_before in ("fixture", "second"):
            for symlink in (True, False):
                with self.subTest(before=swap_before, symlink=symlink):
                    self.new_namespace(f"lock-{swap_before}-{symlink}")
                    lock = samples.sample_lock
                    saved = None

                    def replace_before_lock(root, root_fd, slug, **kwargs):
                        nonlocal saved
                        if slug == swap_before:
                            saved = self.replace(self.root, symlink)
                        return lock(root, root_fd, slug, **kwargs)

                    with (
                        mock.patch.object(samples, "SAMPLE_PROJECTS", projects),
                        mock.patch.object(samples, "sample_lock", side_effect=replace_before_lock),
                    ):
                        with self.assertRaises(OSError):
                            samples.ensure_sample_projects(self.root, force=True)
                    self.assertEqual(sorted(p.name for p in self.foreign.iterdir()), ["fixture"])
                    self.assertEqual((self.foreign / "fixture/value.txt").read_text(), "sentinel")
                    if not symlink:
                        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["sentinel"])
                    self.assertFalse((saved / ".second.lock").exists())
                    self.assertEqual((saved / ".fixture.lock").exists(), swap_before == "second")
                    if symlink:
                        self.root.unlink()

    def test_root_replaced_while_waiting_for_lock_is_refused_after_acquisition(self):
        import fcntl

        with publication._directory(self.root) as root_fd:
            holder = os.open(".fixture.lock", os.O_RDWR | os.O_CREAT, 0o644, dir_fd=root_fd)
            self.addCleanup(os.close, holder)
            fcntl.flock(holder, fcntl.LOCK_EX)
            saved = None
            sleep = time.sleep

            def swap_then_release(seconds):
                nonlocal saved
                if saved is None:
                    saved = self.replace(self.root, False)
                    fcntl.flock(holder, fcntl.LOCK_UN)
                sleep(seconds)

            with mock.patch.object(publication.time, "sleep", side_effect=swap_then_release):
                with self.assertRaises(OSError):
                    with publication.sample_lock(self.root, root_fd, "fixture"):
                        self.fail("lock body must not run after the root name changed")
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["sentinel"])
        self.assertTrue((saved / ".fixture.lock").exists())

    def test_held_root_lock_times_out_and_shares_the_inode_with_filelock(self):
        from filelock import FileLock

        with publication.sample_root_owner(self.root) as root_fd:
            with FileLock(self.root / ".fixture.lock", preserve_lock_file=True):
                with self.assertRaises(TimeoutError):
                    with publication.sample_lock(self.root, root_fd, "fixture", timeout=0.1):
                        self.fail("lock must stay exclusive")
            with publication.sample_lock(self.root, root_fd, "fixture", timeout=0.1):
                pass
