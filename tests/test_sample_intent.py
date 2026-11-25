"""Unsafe or ambiguous recovery controls never authorize mutation."""

import json
import os
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from django.test import SimpleTestCase

from djdesk.inspector import samples


class SampleIntentTests(SimpleTestCase):
    def test_incomplete_or_changed_intent_never_authorizes_recovery(self):
        for fault in ("short read", "in-place edit"):
            with self.subTest(fault=fault), TemporaryDirectory() as temporary:
                root = Path(temporary)
                stage = root / ".fixture-interrupted"
                backup = stage / "backup"
                replacement = stage / "replacement"
                for path, content in ((backup, "old"), (replacement, "new")):
                    path.mkdir(parents=True)
                    (path / "value.txt").write_text(content)

                def identity(path):
                    info = path.stat()
                    return [info.st_dev, info.st_ino]

                intent = {
                    "version": 1,
                    "root": identity(root),
                    "stage": {"name": stage.name, "identity": identity(stage)},
                    "old": identity(backup),
                    "new": identity(replacement),
                }
                journal = root / ".fixture.swap.json"
                original = json.dumps(intent).encode() + b"\n"
                edited = original[:-1] + b" "
                journal.write_bytes(original)
                fdopen = os.fdopen

                @contextmanager
                def inject_fault(fd, mode):
                    with fdopen(fd, mode) as handle:
                        read = handle.read

                        def read_with_fault(size):
                            data = read(size)
                            if fault == "short read":
                                return data[:-1]  # The prefix is still valid JSON.
                            before = journal.stat()
                            journal.write_bytes(edited)
                            os.utime(journal, ns=(before.st_atime_ns, before.st_mtime_ns + 10**9))
                            return data

                        with mock.patch.object(handle, "read", side_effect=read_with_fault):
                            yield handle

                fixture = samples.SampleProject("fixture", "Fixture", "Synthetic", "fixture", {})
                with (
                    mock.patch.object(samples, "SAMPLE_PROJECTS", [fixture]),
                    mock.patch.object(os, "fdopen", side_effect=inject_fault),
                    mock.patch.object(os, "rename", wraps=os.rename) as rename,
                ):
                    with self.assertRaisesRegex(OSError, "manual inspection"):
                        samples.ensure_sample_projects(root, force=True)
                rename.assert_not_called()
                self.assertFalse((root / "fixture").exists())
                self.assertEqual((backup / "value.txt").read_text(), "old")
                self.assertEqual((replacement / "value.txt").read_text(), "new")
                expected = original if fault == "short read" else edited
                self.assertEqual(journal.read_bytes(), expected)

    def test_invalid_intents_preserve_all_paths(self):
        for content in ("{", "[]", '{"version":1,"version":1}', '{"version":2}', "x" * 16385):
            with self.subTest(content=content[:50]), TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root / "fixture").mkdir()
                (root / "fixture/value.txt").write_text("local")
                journal = root / ".fixture.swap.json"
                journal.write_text(content)
                fixture = samples.SampleProject("fixture", "Fixture", "Synthetic", "fixture", {})
                with mock.patch.object(samples, "SAMPLE_PROJECTS", [fixture]):
                    with self.assertRaisesRegex(OSError, "manual inspection"):
                        samples.ensure_sample_projects(root, force=True)
                self.assertEqual(journal.read_text(), content)
                self.assertEqual((root / "fixture/value.txt").read_text(), "local")

    def test_symlinked_intent_does_not_read_or_unlink_its_target(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            foreign = root / "foreign.json"
            foreign.write_text(json.dumps({"version": 1}))
            journal = root / ".fixture.swap.json"
            journal.symlink_to(foreign)
            fixture = samples.SampleProject("fixture", "Fixture", "Synthetic", "fixture", {})
            with mock.patch.object(samples, "SAMPLE_PROJECTS", [fixture]):
                with self.assertRaisesRegex(OSError, "manual inspection"):
                    samples.ensure_sample_projects(root)
            self.assertTrue(journal.is_symlink())
            self.assertEqual(json.loads(foreign.read_text()), {"version": 1})
