from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from django.test import SimpleTestCase, override_settings

from djdesk.inspector.command_runner import UnsafeCommandError, run_command, validate_safe_command


class CommandPolicyTests(SimpleTestCase):
    def test_rejects_file_output_before_spawn(self):
        for option in (
            "--output sentinel",
            "--output=sentinel",
            "-o sentinel",
            "-osentinel",
            "-ao sentinel",
            "-aosentinel",
            "--out sentinel",
        ):
            with self.subTest(option=option), mock.patch("subprocess.Popen") as spawn:
                with self.assertRaises(UnsafeCommandError):
                    run_command(
                        command=f"python manage.py dumpdata {option}",
                        workspace_path="/missing",
                        timeout=1,
                        log_callback=lambda _: None,
                        safe_prefix="trusted",
                    )
                spawn.assert_not_called()

    def test_safe_arguments_and_token_boundaries(self):
        for command in (
            "python manage.py dumpdata auth --indent 2 --exclude auth.permission",
            "python manage.py check --deploy --database default",
        ):
            validate_safe_command(command)
        with self.assertRaises(UnsafeCommandError):
            validate_safe_command('"python manage.py check"')
        with self.assertRaises(UnsafeCommandError):
            validate_safe_command("python manage.py check --unknown")


class CommandCleanupTests(SimpleTestCase):
    def test_callback_exception_reaps_child(self):
        with TemporaryDirectory() as temp:
            path = Path(temp)
            (path / "manage.py").write_text(
                "import time; print('ready', flush=True); time.sleep(30)"
            )
            command = f'"{sys.executable}" manage.py'
            spawned = []
            real_spawn = subprocess.Popen

            def spawn(*args, **kwargs):
                process = real_spawn(*args, **kwargs)
                spawned.append(process)
                return process

            with (
                override_settings(INSPECTOR_SAFE_COMMANDS=[command]),
                mock.patch("djdesk.inspector.command_runner.subprocess.Popen", side_effect=spawn),
            ):
                with self.assertRaisesRegex(RuntimeError, "log unavailable"):
                    run_command(
                        command=command,
                        workspace_path=temp,
                        timeout=10,
                        log_callback=mock.Mock(side_effect=RuntimeError("log unavailable")),
                    )
            self.assertIsNotNone(spawned[0].poll())
            self.assertTrue(spawned[0].stdout.closed)


class AdditionalPolicyTests(SimpleTestCase):
    def test_module_loading_options_are_rejected(self):
        for command in (
            "check --settings payload",
            "check --pythonpath /tmp/payload",
            "diffsettings --default payload",
        ):
            with self.assertRaises(UnsafeCommandError):
                validate_safe_command(f"python manage.py {command}")

    def test_unsupported_allowlist_shapes_fail_closed(self):
        for command in ("python -m django dumpdata", "python manage.py show_urls"):
            with override_settings(INSPECTOR_SAFE_COMMANDS=[command]):
                with self.assertRaises(UnsafeCommandError):
                    validate_safe_command(command + " --output file")
        with override_settings(INSPECTOR_SAFE_COMMANDS=["python manage.py check --deploy"]):
            validate_safe_command("python manage.py check --deploy")
            with self.assertRaises(UnsafeCommandError):
                validate_safe_command("python manage.py check --deploy --settings payload")


class ProcessTreeTests(SimpleTestCase):
    def test_timeout_stops_descendant(self):
        with TemporaryDirectory() as temp:
            path = Path(temp)
            marker = path / "survived"
            script = (
                "import subprocess, sys, time\n"
                "subprocess.Popen([sys.executable, '-c', "
                '"import time; from pathlib import Path; time.sleep(1.5); '
                "Path('survived').write_text('alive')\"])\n"
                "print('spawned', flush=True)\n"
                "time.sleep(30)\n"
            )
            (path / "manage.py").write_text(script)
            command = f'"{sys.executable}" manage.py'
            with override_settings(INSPECTOR_SAFE_COMMANDS=[command]):
                result = run_command(
                    command=command, workspace_path=temp, timeout=0.4, log_callback=lambda _: None
                )
            self.assertTrue(result.timed_out)
            time.sleep(1.6)
            self.assertFalse(marker.exists())

    @mock.patch("djdesk.inspector.command_runner.subprocess.run")
    def test_windows_uses_taskkill_tree(self, taskkill):
        from djdesk.inspector.command_runner import _stop_process_tree

        process = mock.Mock(pid=123)
        process.poll.return_value = None
        with mock.patch("djdesk.inspector.command_runner.os.name", "nt"):
            _stop_process_tree(process)
        self.assertEqual(taskkill.call_args.args[0], ["taskkill", "/PID", "123", "/T", "/F"])
        process.wait.assert_called_once()


class OutputBudgetTests(SimpleTestCase):
    @override_settings(INSPECTOR_COMMAND_OUTPUT_BYTES=8192)
    def test_flood_and_single_line_stop_at_byte_budget(self):
        for content in ("b'x\\n' * 1000000", "b'x' * 3000000"):
            with self.subTest(content=content), TemporaryDirectory() as temp:
                path = Path(temp)
                (path / "manage.py").write_text(
                    "import sys, time\n"
                    + f"sys.stdout.buffer.write({content})\n"
                    + "sys.stdout.flush()\ntime.sleep(30)\n"
                )
                command = f'"{sys.executable}" manage.py'
                output = []
                with override_settings(INSPECTOR_SAFE_COMMANDS=[command]):
                    result = run_command(
                        command=command, workspace_path=temp, timeout=5, log_callback=output.append
                    )
                self.assertTrue(result.output_limited)
                self.assertLess(sum(len(line.encode()) for line in output), 8300)
                self.assertLess(result.duration, 3)
                self.assertIn("truncated", output[-1])
