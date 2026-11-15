"""Real publishers sharing one synthetic destination."""

import os
import subprocess
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from django.test import SimpleTestCase

from djdesk.inspector import samples

PUBLISHER = """
import sys, time
from pathlib import Path
from djdesk.inspector import samples
root, signals, mode = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
samples.SAMPLE_PROJECTS = [samples.SampleProject(
    'fixture', 'Fixture', 'Synthetic', 'fixture', {'value.txt': mode})]
rename = Path.rename
def controlled_rename(path, target):
    if mode == 'second' and path.name == 'replacement':
        raise OSError('second install failed')
    result = rename(path, target)
    if mode == 'first' and path.name == 'fixture':
        (signals / 'paused').touch()
        while not (signals / 'release').exists():
            time.sleep(.01)
    return result
Path.rename = controlled_rename
(signals / (mode + '-started')).touch()
samples.ensure_sample_projects(root, force=True)
"""

CRASH_PUBLISHER = """
import sys, time
from pathlib import Path
from djdesk.inspector import samples
root, signals, boundary = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
samples.SAMPLE_PROJECTS = [samples.SampleProject(
    'fixture', 'Fixture', 'Synthetic', 'fixture', {'value.txt': 'new'})]
rename = Path.rename
def controlled_rename(path, target):
    action = 'backup' if path.name == 'fixture' else 'install'
    def pause(when):
        if boundary == when + '-' + action:
            (signals / 'paused').touch()
            while True:
                time.sleep(.01)
    pause('before')
    result = rename(path, target)
    pause('after')
    return result
Path.rename = controlled_rename
samples.ensure_sample_projects(root, force=True)
"""


class SampleProcessTests(SimpleTestCase):
    def wait_file(self, path, process):
        deadline = time.monotonic() + 10
        while not path.exists():
            if process.poll() is not None or time.monotonic() > deadline:
                self.fail(f"Publisher did not reach {path}: {process.communicate(timeout=1)}")
            time.sleep(0.01)

    def start(self, root, signals, mode, code=PUBLISHER):
        process = subprocess.Popen(
            [sys.executable, "-c", code, str(root), str(signals), mode],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ},
        )
        self.addCleanup(self.stop, process)
        return process

    @staticmethod
    def stop(process):
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)

    def test_failed_waiting_publisher_preserves_completed_winner(self):
        with TemporaryDirectory() as temporary:
            base = Path(temporary)
            root, signals = base / "samples", base / "signals"
            signals.mkdir()
            (root / "fixture").mkdir(parents=True)
            (root / "fixture/value.txt").write_text("original")
            first = self.start(root, signals, "first")
            self.wait_file(signals / "paused", first)
            second = self.start(root, signals, "second")
            self.wait_file(signals / "second-started", second)
            try:
                # A second publisher must wait through the first publisher's swap.
                with self.assertRaises(subprocess.TimeoutExpired):
                    second.wait(timeout=0.5)
            finally:
                (signals / "release").touch()
            self.assertEqual(first.wait(timeout=10), 0, first.communicate())
            self.assertNotEqual(second.wait(timeout=10), 0)
            self.assertEqual((root / "fixture/value.txt").read_text(), "first\n")

    def test_killed_swaps_recover_without_force_and_retain_interrupted_files(self):
        for boundary in ("before-backup", "after-backup", "before-install", "after-install"):
            with self.subTest(boundary=boundary), TemporaryDirectory() as temporary:
                base = Path(temporary)
                root, signals = base / "samples", base / "signals"
                signals.mkdir()
                destination = root / "fixture"
                destination.mkdir(parents=True)
                (destination / "value.txt").write_text("old")
                process = self.start(root, signals, boundary, CRASH_PUBLISHER)
                self.wait_file(signals / "paused", process)
                self.stop(process)
                backups = list(root.glob(".fixture-*/backup"))
                if backups:
                    (backups[0] / "manual.txt").write_text("recovery edit")
                fixture = samples.SampleProject(
                    "fixture", "Fixture", "Synthetic", "fixture", {"value.txt": "retry"}
                )
                with mock.patch.object(samples, "SAMPLE_PROJECTS", [fixture]):
                    with self.assertWarnsRegex(RuntimeWarning, "Retained interrupted sample"):
                        result = samples.ensure_sample_projects(root)
                self.assertEqual(result, [("fixture", destination, False)])
                expected = "new\n" if boundary == "after-install" else "old"
                self.assertEqual((destination / "value.txt").read_text(), expected)
                if backups:
                    retained = backups[0] if backups[0].exists() else destination
                    self.assertEqual((retained / "manual.txt").read_text(), "recovery edit")
                self.assertFalse((root / ".fixture.swap.json").exists())

    def test_foreign_destination_blocks_recovery_without_moving_files(self):
        with TemporaryDirectory() as temporary:
            base = Path(temporary)
            root, signals = base / "samples", base / "signals"
            signals.mkdir()
            destination = root / "fixture"
            destination.mkdir(parents=True)
            (destination / "value.txt").write_text("old")
            process = self.start(root, signals, "after-backup", CRASH_PUBLISHER)
            self.wait_file(signals / "paused", process)
            self.stop(process)
            destination.mkdir()
            (destination / "foreign.txt").write_text("foreign")
            fixture = samples.SampleProject("fixture", "Fixture", "Synthetic", "fixture", {})
            with mock.patch.object(samples, "SAMPLE_PROJECTS", [fixture]):
                with self.assertRaisesRegex(OSError, "manual inspection"):
                    samples.ensure_sample_projects(root, force=True)
            self.assertEqual((destination / "foreign.txt").read_text(), "foreign")
            self.assertEqual(next(root.glob(".fixture-*/backup/value.txt")).read_text(), "old")

    def test_killed_first_install_completes_the_owned_generation(self):
        with TemporaryDirectory() as temporary:
            base = Path(temporary)
            root, signals = base / "samples", base / "signals"
            signals.mkdir()
            process = self.start(root, signals, "before-install", CRASH_PUBLISHER)
            self.wait_file(signals / "paused", process)
            self.stop(process)
            fixture = samples.SampleProject("fixture", "Fixture", "Synthetic", "fixture", {})
            with mock.patch.object(samples, "SAMPLE_PROJECTS", [fixture]):
                with self.assertWarns(RuntimeWarning):
                    result = samples.ensure_sample_projects(root)
            self.assertFalse(result[0][2])
            self.assertEqual((root / "fixture/value.txt").read_text(), "new\n")

    def test_force_restart_does_not_immediately_delete_the_recovered_backup(self):
        with TemporaryDirectory() as temporary:
            base = Path(temporary)
            root, signals = base / "samples", base / "signals"
            signals.mkdir()
            (root / "fixture").mkdir(parents=True)
            (root / "fixture/value.txt").write_text("old")
            process = self.start(root, signals, "after-backup", CRASH_PUBLISHER)
            self.wait_file(signals / "paused", process)
            self.stop(process)
            backup = next(root.glob(".fixture-*/backup"))
            (backup / "manual.txt").write_text("recovery edit")
            fixture = samples.SampleProject(
                "fixture", "Fixture", "Synthetic", "fixture", {"value.txt": "retry"}
            )
            with mock.patch.object(samples, "SAMPLE_PROJECTS", [fixture]):
                with self.assertWarns(RuntimeWarning):
                    result = samples.ensure_sample_projects(root, force=True)
            self.assertFalse(result[0][2])
            self.assertEqual((root / "fixture/manual.txt").read_text(), "recovery edit")
            self.assertEqual((root / "fixture/value.txt").read_text(), "old")

    def test_changed_recovery_identities_are_retained_for_manual_inspection(self):
        for name in ("backup", "replacement", "stage"):
            with self.subTest(path=name), TemporaryDirectory() as temporary:
                base = Path(temporary)
                root, signals = base / "samples", base / "signals"
                signals.mkdir()
                (root / "fixture").mkdir(parents=True)
                (root / "fixture/value.txt").write_text("old")
                process = self.start(root, signals, "after-backup", CRASH_PUBLISHER)
                self.wait_file(signals / "paused", process)
                self.stop(process)
                stage = next(root.glob(".fixture-*"))
                changed = stage if name == "stage" else stage / name
                saved = changed.with_name(changed.name + "-saved")
                changed.rename(saved)
                changed.mkdir()
                (changed / "foreign.txt").write_text("foreign")
                fixture = samples.SampleProject("fixture", "Fixture", "Synthetic", "fixture", {})
                with mock.patch.object(samples, "SAMPLE_PROJECTS", [fixture]):
                    with self.assertRaisesRegex(OSError, "manual inspection"):
                        samples.ensure_sample_projects(root)
                self.assertTrue(saved.exists())
                self.assertEqual((changed / "foreign.txt").read_text(), "foreign")
                self.assertFalse((root / "fixture").exists())
