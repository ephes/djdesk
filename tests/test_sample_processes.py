"""Real publishers sharing one synthetic destination."""

import os
import subprocess
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import SimpleTestCase

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
