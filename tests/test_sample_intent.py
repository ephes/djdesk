"""Unsafe or ambiguous recovery controls never authorize mutation."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from django.test import SimpleTestCase

from djdesk.inspector import samples


class SampleIntentTests(SimpleTestCase):
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
