"""Synthetic filesystem and spawned-process contracts for the docs publisher."""

import multiprocessing
import os
import shutil
import tempfile
from pathlib import Path
from unittest import TestCase, mock

from filelock import FileLock, Timeout

from scripts import bundle_docs


def _publish_worker(source, dest, messages, release):
    copytree = shutil.copytree

    def paused_copy(source, staging, *args, **kwargs):
        result = copytree(source, staging, *args, **kwargs)
        messages.put(("copied", str(staging)))
        if not release.wait(15):
            raise TimeoutError("copy barrier timed out")
        return result

    try:
        with mock.patch.object(bundle_docs.shutil, "copytree", side_effect=paused_copy):
            bundle_docs._prepare_destination(Path(source), Path(dest))
        messages.put(("done", source))
    except BaseException as error:
        messages.put(("error", repr(error)))


class BundleDocsTests(TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "html"
        self.dest = self.root / "bundle"
        self.source.mkdir()
        self.dest.mkdir()
        (self.source / "index.html").write_text("new", encoding="utf-8")
        (self.dest / "index.html").write_text("old", encoding="utf-8")

    def assert_old(self):
        self.assertEqual((self.dest / "index.html").read_text(encoding="utf-8"), "old")

    def assert_no_staging(self):
        self.assertEqual(list(self.root.glob(".bundle-publish-*")), [])

    def test_copy_failure_preserves_live_and_unowned_paths(self):
        legacy = [self.root / ".bundle.tmp", self.root / ".bundle.old"]
        for path in legacy:
            path.mkdir()
            (path / "sentinel").write_text("keep", encoding="utf-8")

        def fail_copy(source, staging):
            staging.mkdir()
            raise OSError("copy failed")

        with mock.patch.object(bundle_docs.shutil, "copytree", side_effect=fail_copy):
            with self.assertRaisesRegex(OSError, "copy failed"):
                bundle_docs._prepare_destination(self.source, self.dest)
        self.assert_old()
        self.assert_no_staging()
        for path in legacy:
            self.assertEqual((path / "sentinel").read_text(encoding="utf-8"), "keep")

    def test_swap_failures_restore_old_or_report_recovery(self):
        for failures in ({1}, {2}, {2, 3}):
            with self.subTest(failures=failures):
                calls = []
                replace = os.replace
                original = OSError("original swap failure")

                def fail_replace(source, dest):
                    calls.append((source, dest))
                    if len(calls) in failures:
                        raise original if len(calls) < 3 else OSError("restore failed")
                    return replace(source, dest)

                with mock.patch.object(bundle_docs.os, "replace", side_effect=fail_replace):
                    with self.assertRaises(OSError) as caught:
                        bundle_docs._prepare_destination(self.source, self.dest)
                self.assertIs(caught.exception, original)
                self.assertEqual(len(calls), 1 if failures == {1} else 3)
                if failures == {2, 3}:
                    backup = next(self.root.glob(".bundle-publish-*/backup"))
                    self.assertFalse(self.dest.exists())
                    self.assertEqual((backup / "index.html").read_text(), "old")
                    self.assertIn(str(backup), " ".join(caught.exception.__notes__))
                    self.assertIn("restore failed", " ".join(caught.exception.__notes__))
                else:
                    self.assert_old()
                    self.assert_no_staging()

    def test_lock_timeout_and_interruption_preserve_live(self):
        lock_path = self.root / ".bundle.lock"
        with FileLock(lock_path):
            with mock.patch.object(
                bundle_docs, "FileLock", side_effect=lambda path, **kw: FileLock(path, timeout=0)
            ):
                with self.assertRaises(Timeout):
                    bundle_docs._prepare_destination(self.source, self.dest)
        self.assert_old()
        self.assert_no_staging()
        with mock.patch.object(bundle_docs, "FileLock", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                bundle_docs._prepare_destination(self.source, self.dest)
        self.assert_old()
        self.assert_no_staging()

    def test_interruption_during_promotion_restores_live(self):
        replace = os.replace

        def interrupt(source, dest):
            if Path(source).name == "staging":
                raise KeyboardInterrupt
            return replace(source, dest)

        with mock.patch.object(bundle_docs.os, "replace", side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                bundle_docs._prepare_destination(self.source, self.dest)
        self.assert_old()
        self.assert_no_staging()

    def test_cleanup_failure_does_not_offer_incomplete_backup_for_recovery(self):
        error = OSError("cleanup failed")

        def fail_cleanup(work):
            (work / "backup" / "index.html").unlink()
            raise error

        with mock.patch.object(bundle_docs.shutil, "rmtree", side_effect=fail_cleanup):
            with self.assertRaises(OSError) as caught:
                bundle_docs._prepare_destination(self.source, self.dest)
        self.assertIs(caught.exception, error)
        self.assertEqual((self.dest / "index.html").read_text(), "new")
        notes = " ".join(error.__notes__)
        self.assertIn("New bundle installed", notes)
        self.assertIn("may be incomplete", notes)
        self.assertNotIn("Recovery copy", notes)

    def test_two_spawned_publishers_own_distinct_generations(self):
        other = self.root / "other-html"
        other.mkdir()
        (other / "index.html").write_text("other", encoding="utf-8")
        for source in (self.source, other):
            (source / "asset.txt").write_text((source / "index.html").read_text())
        ctx = multiprocessing.get_context("spawn")
        messages = ctx.Queue()
        release = ctx.Event()
        workers = [
            ctx.Process(
                target=_publish_worker, args=(str(source), str(self.dest), messages, release)
            )
            for source in (self.source, other)
        ]
        try:
            for worker in workers:
                worker.start()
            copied = [messages.get(timeout=20), messages.get(timeout=20)]
            self.assertEqual([item[0] for item in copied], ["copied", "copied"], copied)
            self.assertNotEqual(copied[0][1], copied[1][1])
            for _, path in copied:
                self.assertTrue((Path(path) / "index.html").is_file())
            self.assert_old()
            release.set()
            done = [messages.get(timeout=20), messages.get(timeout=20)]
            self.assertEqual([item[0] for item in done], ["done", "done"], done)
            for worker in workers:
                worker.join(timeout=20)
                self.assertEqual(worker.exitcode, 0)
        finally:
            release.set()
            for worker in workers:
                if worker.is_alive():
                    worker.terminate()
                worker.join(timeout=5)
            messages.close()
            messages.join_thread()
        content = (self.dest / "index.html").read_text()
        self.assertIn(content, {"new", "other"})
        self.assertEqual((self.dest / "asset.txt").read_text(), content)
        self.assert_no_staging()
