"""Synthetic filesystem and spawned-process contracts for the docs publisher."""

import io
import json
import multiprocessing
import os
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace
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
        winning_source = self.source if content == "new" else other
        self.assertEqual(
            json.loads((self.dest / "bundle.json").read_text())["source"],
            str(winning_source.resolve()),
        )
        self.assert_no_staging()

    def _assert_refused(self, source, dest, error=ValueError):
        before = {
            p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()
        }
        with (
            mock.patch.object(bundle_docs.shutil, "copytree") as copy,
            mock.patch.object(bundle_docs.os, "replace") as replace,
            mock.patch.object(bundle_docs.shutil, "rmtree") as cleanup,
            mock.patch.object(bundle_docs.tempfile, "mkdtemp") as staging,
            mock.patch.object(
                Path, "mkdir", side_effect=AssertionError("mkdir attempted before refusal")
            ) as mkdir,
        ):
            with self.assertRaises(error):
                bundle_docs._prepare_destination(source, dest)
            copy.assert_not_called()
            replace.assert_not_called()
            cleanup.assert_not_called()
            staging.assert_not_called()
            mkdir.assert_not_called()
        after = {
            p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()
        }
        self.assertEqual(after, before)

    def test_overlapping_trees_refused_before_mutation(self):
        nested_source = self.dest / "source"
        nested_source.mkdir()
        (nested_source / "sentinel").write_text("keep", encoding="utf-8")
        for source, dest in (
            (self.source, self.source),
            (nested_source, self.dest),
            (self.source, self.source / "nested"),
            (self.source, self.source / "missing" / "nested"),
        ):
            with self.subTest(source=source, dest=dest):
                self._assert_refused(source, dest)

    def test_case_alias_identity_refused_on_any_filesystem(self):
        source = self.root / "logical" / "html"
        alias = self.root / "logical" / "HTML"
        (self.source / "child").mkdir()
        stat = Path.stat
        exists = Path.exists
        is_dir = Path.is_dir

        def physical_path(path):
            for spelling in (source, alias):
                if path == spelling or spelling in path.parents:
                    return self.source / path.relative_to(spelling)
            return path

        def case_alias_stat(path, *args, **kwargs):
            return stat(physical_path(path), *args, **kwargs)

        # Model a filesystem where realpath preserves spelling but stat reports
        # the same identity for case aliases. No host case-sensitivity required.
        with (
            mock.patch.object(Path, "resolve", autospec=True, side_effect=lambda path, **kw: path),
            mock.patch.object(Path, "stat", autospec=True, side_effect=case_alias_stat),
            mock.patch.object(
                Path, "exists", autospec=True, side_effect=lambda path: exists(physical_path(path))
            ),
            mock.patch.object(
                Path, "is_dir", autospec=True, side_effect=lambda path: is_dir(physical_path(path))
            ),
        ):
            for tree, dest in (
                (source, alias),
                (source, alias / "missing" / "nested"),
                (source / "child", alias),
                (alias, source / "nested"),
            ):
                with self.subTest(source=tree, dest=dest):
                    self._assert_refused(tree, dest)

    def test_resolved_aliases_refused(self):
        # Simulate directory-symlink resolution without requiring symlink privileges.
        alias = self.root / "alias"
        resolve = Path.resolve

        def resolve_alias(path, *args, **kwargs):
            return self.dest.resolve() if path == alias else resolve(path, *args, **kwargs)

        with mock.patch.object(Path, "resolve", autospec=True, side_effect=resolve_alias):
            self._assert_refused(alias, self.dest)
            self._assert_refused(self.dest, alias)

    def test_source_must_be_real_directory(self):
        self._assert_refused(self.root / "missing", self.dest, FileNotFoundError)
        self._assert_refused(self.source / "index.html", self.dest, NotADirectoryError)

    def _run_main(self, dest=None):
        args = SimpleNamespace(source=self.source, dest=dest or self.dest)
        with (
            mock.patch.object(bundle_docs, "parse_args", return_value=args),
            mock.patch("sys.stdout", new_callable=io.StringIO),
        ):
            bundle_docs.main()

    def test_manifest_failures_leave_old_generation_paired(self):
        (self.dest / "bundle.json").write_text('{"generation": "old"}', encoding="utf-8")
        write_text = Path.write_text
        for failure in ("serialize", "write"):
            with self.subTest(failure=failure):
                error = OSError(f"{failure} failed")

                def fail_write(path, *args, **kwargs):
                    if path.name == "bundle.json":
                        # Model a partial write, not just failure before opening.
                        write_text(path, "partial", encoding="utf-8")
                        raise error
                    return write_text(path, *args, **kwargs)

                patcher = (
                    mock.patch.object(bundle_docs.json, "dumps", side_effect=error)
                    if failure == "serialize"
                    else mock.patch.object(
                        Path, "write_text", autospec=True, side_effect=fail_write
                    )
                )
                with patcher as injected:
                    with self.assertRaises(OSError) as caught:
                        self._run_main()
                self.assertIs(caught.exception, error)
                injected.assert_called_once()
                self.assert_old()
                self.assertEqual(
                    json.loads((self.dest / "bundle.json").read_text()), {"generation": "old"}
                )
                self.assert_no_staging()

    def test_custom_destination_publishes_html_and_manifest_together(self):
        dest = (self.root / "custom" / "offline").resolve()
        replace = os.replace
        promotions = []

        def promote(source, target):
            if Path(target) == dest:
                promotions.append(source)
                self.assertEqual((Path(source) / "index.html").read_text(), "new")
                manifest = json.loads((Path(source) / "bundle.json").read_text())
                self.assertEqual(manifest["source"], str(self.source.resolve()))
                self.assertTrue(manifest["generated_at"].endswith("+00:00"))
            return replace(source, target)

        with mock.patch.object(bundle_docs.os, "replace", side_effect=promote):
            self._run_main(dest=dest)
        self.assertEqual(len(promotions), 1)
        self.assertEqual((dest / "index.html").read_text(), "new")
        self.assertEqual(
            json.loads((dest / "bundle.json").read_text())["source"], str(self.source.resolve())
        )
