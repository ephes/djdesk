"""Command guard and process-ownership tests for the workspace command runner."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock, skipUnless

from django.test import SimpleTestCase, TestCase, override_settings

from djdesk.inspector import command_runner
from djdesk.inspector.command_runner import (
    UnsafeCommandError,
    run_command,
    validate_argv,
    validate_safe_command,
)
from djdesk.inspector.models import TaskPreset, Workspace, WorkspaceTaskRun
from djdesk.inspector.tasks import execute_workspace_task

MANAGE = "python manage.py"


class CommandGuardTests(SimpleTestCase):
    def test_refuses_every_file_output_spelling_before_spawning(self) -> None:
        spellings = [
            "--output sentinel.json",
            "--output=sentinel.json",
            "-o sentinel.json",
            "-osentinel.json",
            "-o=sentinel.json",
            "-ao sentinel.json",
            "-aosentinel.json",
            "--out sentinel.json",
            "--outp=sentinel.json",
            "auth --output sentinel.json",
            "--indent 2 -o sentinel.json",
            "-- --output sentinel.json",
            "'--output=sentinel one.json'",
        ]
        with TemporaryDirectory() as workspace:
            (Path(workspace) / "manage.py").write_text("raise SystemExit('must not run')\n")
            for spelling in spellings:
                command = f"{MANAGE} dumpdata {spelling}"
                with self.subTest(command=command), mock.patch("subprocess.Popen") as spawn:
                    with self.assertRaises(UnsafeCommandError):
                        run_command(
                            command=command,
                            workspace_path=workspace,
                            timeout=5,
                            log_callback=lambda line: None,
                        )
                    spawn.assert_not_called()
            self.assertEqual(os.listdir(workspace), ["manage.py"])

    def test_refuses_argv_that_only_looks_safe_as_a_joined_string(self) -> None:
        # Each list joins to a string starting with an allowlisted prefix; none of them is
        # an allowlisted argv.
        for argv in (
            ["python", "manage.py", "dumpdata --output=sentinel.json"],
            ["python", "manage.py dumpdata", "--output=sentinel.json"],
            ["python manage.py dumpdata", "--output=sentinel.json"],
            ["python manage.py", "dumpdata"],
            ["python", "manage.py", "check extra"],
            ["python", "manage.py", "dumpdata", "auth --output=sentinel.json"],
            ["python", "manage.py", "dumpdata", "--indent", "2 --output=sentinel.json"],
            ["python", "-c", "import os", "manage.py", "check"],
            ["python", "./manage.py", "check"],
            ["/tmp/python", "manage.py", "check"],
        ):
            with self.subTest(argv=argv):
                with self.assertRaises(UnsafeCommandError):
                    validate_argv(argv)
                with self.assertRaises(UnsafeCommandError):
                    validate_safe_command(shlex.join(argv))

    def test_refuses_options_outside_the_command_policy(self) -> None:
        for arguments in (
            "check --settings=evil.settings",
            "check --settings evil.settings",
            "check --pythonpath /tmp/evil",
            "check --pythonpath=/tmp/evil",
            "diffsettings --default evil.settings",
            "diffsettings --output /tmp/sentinel",
            "diffsettings extra",
            "check --database",
            "check --database --deploy",
            "check --database=",
            "check --deploy=yes",
            "check --tag=-o",
            "check -v3",
            "check -v=3",
            "check --verbosity 9",
            "check --verb 1",
            "check -",
            "check ''",
            "check ../outside",
            "check 'auth\n'",
            "sqlmigrate auth 0001 extra",
            "sqlmigrate auth ../0001",
            "dumpdata --format python",
            "dumpdata --format=/tmp/module",
            "dumpdata --exclude -o",
            "dumpdata --pks=-o",
            "dumpdata auth.User.extra",
            "showmigrations --format json",
            "inspectdb --output x",
            "check --help",
            "check --version",
        ):
            with self.subTest(arguments=arguments), self.assertRaises(UnsafeCommandError):
                validate_safe_command(f"{MANAGE} {arguments}")

    def test_accepts_documented_read_only_arguments(self) -> None:
        for arguments, prefix in (
            ("check", "check"),
            ("check --deploy --fail-level ERROR", "check"),
            ("check auth sessions --tag security -t models --database default", "check"),
            ("check --verbosity 2 --traceback --no-color", "check"),
            ("check -v 0 --force-color --list-tags", "check"),
            ("showmigrations", "showmigrations"),
            ("showmigrations auth --plan --database=default", "showmigrations"),
            ("showmigrations -l", "showmigrations"),
            ("diffsettings --all --output unified", "diffsettings"),
            ("diffsettings --output=hash", "diffsettings"),
            ("sqlmigrate auth 0001_initial --backwards", "sqlmigrate"),
            ("inspectdb auth_user --include-views --include-partitions", "inspectdb"),
            ("dumpdata", "dumpdata"),
            ("dumpdata auth auth.Permission", "dumpdata"),
            ("dumpdata auth --indent 2 --format=json", "dumpdata"),
            ("dumpdata -a --natural-foreign --natural-primary", "dumpdata"),
            ("dumpdata --exclude contenttypes -e auth.Permission", "dumpdata"),
            ("dumpdata auth.User --pks 1,2,3 --database replica-1", "dumpdata"),
        ):
            with self.subTest(arguments=arguments):
                self.assertEqual(
                    validate_safe_command(f"{MANAGE} {arguments}"), f"{MANAGE} {prefix}"
                )

    def test_allowlist_entries_must_be_interpreter_manage_py_known_command(self) -> None:
        for entry in (
            "python manage.py",
            "python manage.py check --deploy",
            "python other.py check",
            "python manage.py migrate",
            "ls",
        ):
            with self.subTest(entry=entry), override_settings(INSPECTOR_SAFE_COMMANDS=[entry]):
                with self.assertRaises(UnsafeCommandError):
                    validate_safe_command(entry)
        with override_settings(INSPECTOR_SAFE_COMMANDS=["python manage.py check"]):
            with self.assertRaises(UnsafeCommandError):
                validate_safe_command("python manage.py dumpdata")

    def test_executes_exactly_the_argv_that_was_validated(self) -> None:
        validated: list[list[str]] = []
        real_validate = validate_argv

        def spy(argv):
            validated.append(argv)
            return real_validate(argv)

        with (
            TemporaryDirectory() as workspace,
            mock.patch.object(command_runner, "validate_argv", side_effect=spy),
            mock.patch("subprocess.Popen", side_effect=OSError("stop here")) as spawn,
        ):
            (Path(workspace) / "manage.py").write_text("")
            with self.assertRaises(command_runner.CommandExecutionError):
                run_command(
                    command="python  manage.py dumpdata 'auth' --indent=2",
                    workspace_path=workspace,
                    timeout=5,
                    log_callback=lambda line: None,
                )
        self.assertEqual(validated, [["python", "manage.py", "dumpdata", "auth", "--indent=2"]])
        self.assertIs(spawn.call_args.args[0], validated[0])
        self.assertNotIn("shell", spawn.call_args.kwargs)

    def test_caller_cannot_supply_a_prevalidated_prefix(self) -> None:
        with TemporaryDirectory() as workspace, mock.patch("subprocess.Popen") as spawn:
            (Path(workspace) / "manage.py").write_text("")
            with self.assertRaises((UnsafeCommandError, TypeError)):
                run_command(
                    command=f"{MANAGE} dumpdata --output=sentinel.json",
                    workspace_path=workspace,
                    timeout=5,
                    log_callback=lambda line: None,
                    safe_prefix=f"{MANAGE} dumpdata",
                )
            spawn.assert_not_called()


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_gone(pid: int, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.02)
    return False


SLEEPER = "import time; time.sleep(120)"


@skipUnless(os.name == "posix", "process-group fixtures need POSIX sessions")
class ProcessFixtureCase(SimpleTestCase):
    """Run real fixture scripts as ``<this interpreter> manage.py check``."""

    def setUp(self) -> None:
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.workspace = Path(temp.name)
        self.command = shlex.join([sys.executable, "manage.py", "check"])
        allow = override_settings(INSPECTOR_SAFE_COMMANDS=[self.command])
        allow.enable()
        self.addCleanup(allow.disable)
        grace = mock.patch.object(command_runner, "TERMINATION_GRACE_SECONDS", 0.5)
        grace.start()
        self.addCleanup(grace.stop)

        self.spawned: list[subprocess.Popen] = []
        real_popen = subprocess.Popen

        def recording_popen(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            self.spawned.append(process)
            return process

        spawn = mock.patch("subprocess.Popen", side_effect=recording_popen)
        spawn.start()
        self.addCleanup(spawn.stop)
        self.addCleanup(self._kill_leftovers)
        self.threads_before = threading.active_count()

    def _kill_leftovers(self) -> None:
        for pid in self._recorded_pids():
            try:
                os.kill(pid, 9)
            except (ProcessLookupError, PermissionError):
                pass
        for process in self.spawned:
            if process.returncode is None:
                process.kill()
                process.wait()
            if process.stdout is not None:
                process.stdout.close()

    def _recorded_pids(self) -> list[int]:
        path = self.workspace / "pids"
        if not path.exists():
            return []
        return [int(value) for value in path.read_text().split()]

    def write_manage(self, body: str) -> None:
        prelude = (
            "import os, signal, subprocess, sys, time\n"
            f"SLEEPER = [sys.executable, '-c', {SLEEPER!r}]\n"
            "def record(pid):\n"
            "    with open('pids', 'a') as handle:\n"
            "        handle.write(f'{pid}\\n')\n"
            "record(os.getpid())\n"
        )
        (self.workspace / "manage.py").write_text(prelude + body, encoding="utf-8")

    def run_fixture(self, *, timeout: float = 1.0, log_callback=None):
        self.lines: list[str] = []
        return run_command(
            command=self.command,
            workspace_path=str(self.workspace),
            timeout=timeout,
            log_callback=log_callback or self.lines.append,
        )

    def assert_fully_cleaned_up(self) -> None:
        (process,) = self.spawned
        self.assertIsNotNone(process.returncode, "direct child was not reaped")
        self.assertTrue(process.stdout.closed, "output pipe was left open")
        self.assertEqual(threading.active_count(), self.threads_before, "reader thread leaked")
        for pid in self._recorded_pids():
            self.assertTrue(_wait_gone(pid), f"process {pid} survived")


class ProcessTreeTests(ProcessFixtureCase):
    def test_streams_output_and_reports_exit_code(self) -> None:
        self.write_manage("print('one')\nprint('two\\r')\nsys.stdout.write('last')\nsys.exit(3)\n")
        result = self.run_fixture(timeout=10)
        self.assertEqual(self.lines, ["one", "two", "last"])
        self.assertEqual((result.exit_code, result.output_lines, result.timed_out), (3, 3, False))
        self.assertEqual(result.safe_prefix, self.command)
        self.assert_fully_cleaned_up()

    def test_timeout_kills_grandchild_that_inherited_the_pipe(self) -> None:
        self.write_manage(
            "child = subprocess.Popen(SLEEPER)\n"
            "record(child.pid)\n"
            "print('ready', flush=True)\n"
            "time.sleep(120)\n"
        )
        started = time.monotonic()
        result = self.run_fixture(timeout=1)
        self.assertLess(time.monotonic() - started, 10)
        self.assertTrue(result.timed_out)
        self.assertEqual(len(self._recorded_pids()), 2)
        self.assertEqual(self.lines[0], "ready")
        self.assertIn("Timeout reached", self.lines[-1])
        self.assert_fully_cleaned_up()

    def test_timeout_kills_grandchild_detached_from_the_pipe(self) -> None:
        self.write_manage(
            "child = subprocess.Popen(SLEEPER, stdout=subprocess.DEVNULL,"
            " stderr=subprocess.DEVNULL)\n"
            "record(child.pid)\n"
            "time.sleep(120)\n"
        )
        result = self.run_fixture(timeout=1)
        self.assertTrue(result.timed_out)
        self.assertEqual(len(self._recorded_pids()), 2)
        self.assert_fully_cleaned_up()

    def test_terminates_before_killing(self) -> None:
        self.write_manage(
            "def on_term(signum, frame):\n"
            "    open('terminated', 'w').close()\n"
            "    sys.exit(0)\n"
            "signal.signal(signal.SIGTERM, on_term)\n"
            "print('ready', flush=True)\n"
            "time.sleep(120)\n"
        )
        with mock.patch.object(command_runner, "TERMINATION_GRACE_SECONDS", 10):
            started = time.monotonic()
            result = self.run_fixture(timeout=1)
        self.assertLess(time.monotonic() - started, 8, "grace period was not cut short")
        self.assertTrue(result.timed_out)
        self.assertTrue((self.workspace / "terminated").exists())
        self.assertEqual(result.exit_code, 0)
        self.assert_fully_cleaned_up()

    def test_kills_tree_that_ignores_sigterm(self) -> None:
        self.write_manage(
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "child = subprocess.Popen(SLEEPER)\n"
            "record(child.pid)\n"
            "print('ready', flush=True)\n"
            "time.sleep(120)\n"
        )
        result = self.run_fixture(timeout=1)
        self.assertTrue(result.timed_out)
        self.assertEqual(result.exit_code, -9)
        self.assert_fully_cleaned_up()

    def test_orphan_holding_the_pipe_after_the_child_exits_is_stopped(self) -> None:
        self.write_manage("child = subprocess.Popen(SLEEPER)\nrecord(child.pid)\nprint('done')\n")
        started = time.monotonic()
        result = self.run_fixture(timeout=1)
        self.assertLess(time.monotonic() - started, 10)
        self.assertTrue(result.timed_out, "output was still open at the deadline")
        self.assertEqual(self.lines[0], "done")
        self.assertEqual(len(self._recorded_pids()), 2)
        self.assert_fully_cleaned_up()

    def test_descendant_left_behind_by_a_finished_command_is_stopped(self) -> None:
        self.write_manage(
            "child = subprocess.Popen(SLEEPER, stdout=subprocess.DEVNULL,"
            " stderr=subprocess.DEVNULL)\n"
            "record(child.pid)\n"
            "print('done')\n"
        )
        result = self.run_fixture(timeout=30)
        self.assertEqual((result.exit_code, result.timed_out), (0, False))
        self.assertLess(result.duration, 20)
        self.assertEqual(len(self._recorded_pids()), 2)
        self.assert_fully_cleaned_up()

    def test_pipe_is_closed_when_an_escaped_process_keeps_it_open(self) -> None:
        # A descendant that starts its own session is out of reach of the group signal.
        # The runner must still return, close its end of the pipe and reap its child.
        self.write_manage(
            "child = subprocess.Popen(SLEEPER, start_new_session=True)\n"
            "open('escaped', 'w').write(str(child.pid))\n"
            "print('done', flush=True)\n"
            "time.sleep(120)\n"
        )
        try:
            started = time.monotonic()
            result = self.run_fixture(timeout=1)
            self.assertLess(time.monotonic() - started, 10)
            self.assertTrue(result.timed_out)
            self.assert_fully_cleaned_up()
        finally:
            os.kill(int((self.workspace / "escaped").read_text()), 9)

    def test_command_that_closes_output_and_keeps_running_times_out(self) -> None:
        self.write_manage(
            "print('closing', flush=True)\n"
            "os.close(1)\n"
            "os.close(2)\n"
            "time.sleep(120)\n"
        )
        result = self.run_fixture(timeout=1)
        self.assertTrue(result.timed_out)
        self.assertEqual(self.lines[0], "closing")
        self.assert_fully_cleaned_up()

    @override_settings(INSPECTOR_COMMAND_OUTPUT_BYTES=10**12)
    def test_continuous_output_does_not_defeat_the_timeout(self) -> None:
        self.write_manage("while True:\n    print('x' * 100, flush=True)\n")
        started = time.monotonic()
        result = self.run_fixture(timeout=1, log_callback=lambda line: None)
        self.assertLess(time.monotonic() - started, 10)
        self.assertTrue(result.timed_out)
        self.assert_fully_cleaned_up()

    def test_output_is_decoded_with_the_locale_encoding(self) -> None:
        self.write_manage("sys.stdout.buffer.write(b'caf\\xe9\\n\\x81 end\\n')\n")
        with mock.patch.object(command_runner, "_output_encoding", return_value="cp1252"):
            self.run_fixture(timeout=10)
        self.assertEqual(self.lines, ["café", "\ufffd end"])

    def test_output_encoding_matches_text_mode_pipes(self) -> None:
        with mock.patch.object(command_runner.locale, "getencoding", return_value="cp1252"):
            with mock.patch.object(command_runner.sys, "flags", mock.Mock(utf8_mode=0)):
                self.assertEqual(command_runner._output_encoding(), "cp1252")
            # Python UTF-8 mode overrides the locale, as it does for text=True pipes.
            with mock.patch.object(command_runner.sys, "flags", mock.Mock(utf8_mode=1)):
                self.assertEqual(command_runner._output_encoding(), "utf-8")

    def test_multibyte_characters_split_across_reads_are_preserved(self) -> None:
        self.write_manage(
            "for byte in 'é€'.encode('utf-8'):\n"
            "    sys.stdout.buffer.write(bytes([byte]))\n"
            "    sys.stdout.buffer.flush()\n"
            "    time.sleep(0.05)\n"
        )
        with mock.patch.object(command_runner, "_output_encoding", return_value="utf-8"):
            self.run_fixture(timeout=10)
        self.assertEqual(self.lines, ["é€"])

    def test_command_has_no_stdin(self) -> None:
        self.write_manage("print(repr(sys.stdin.read()))\n")
        result = self.run_fixture(timeout=10)
        self.assertEqual((self.lines, result.exit_code), (["''"], 0))


class CallbackCleanupTests(ProcessFixtureCase):
    def test_callback_error_stops_the_tree_and_propagates_unchanged(self) -> None:
        self.write_manage(
            "child = subprocess.Popen(SLEEPER)\n"
            "record(child.pid)\n"
            "print('ready', flush=True)\n"
            "time.sleep(120)\n"
        )
        failure = RuntimeError("sink failed")

        def sink(line: str) -> None:
            raise failure

        with self.assertRaises(RuntimeError) as caught:
            self.run_fixture(timeout=30, log_callback=sink)
        self.assertIs(caught.exception, failure)
        self.assertEqual(len(self._recorded_pids()), 2)
        self.assert_fully_cleaned_up()

    def test_cancellation_stops_the_tree(self) -> None:
        self.write_manage(
            "child = subprocess.Popen(SLEEPER)\n"
            "record(child.pid)\n"
            "print('ready', flush=True)\n"
            "time.sleep(120)\n"
        )

        def cancel(line: str) -> None:
            raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            self.run_fixture(timeout=30, log_callback=cancel)
        self.assert_fully_cleaned_up()


class TaskFailureRecordingTests(ProcessFixtureCase, TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.workspace_record = Workspace.objects.create(
            name="Fixture",
            project_path=str(self.workspace),
            metadata={"recent_activity": [], "schema": {"nodes": []}},
        )
        self.preset = TaskPreset.objects.create(
            key="fixture-check", label="Fixture", description="Fixture", command=self.command
        )
        self.write_manage("print('ready', flush=True)\ntime.sleep(120)\n")

    def _failing_sink(self):
        real_append = WorkspaceTaskRun.append_log

        def sink(instance, message):
            if message == "ready":
                raise RuntimeError("sink failed")
            return real_append(instance, message)

        return mock.patch.object(WorkspaceTaskRun, "append_log", autospec=True, side_effect=sink)

    def test_callback_failure_fails_the_run_and_keeps_the_original_error(self) -> None:
        run = WorkspaceTaskRun.objects.create(workspace=self.workspace_record, preset=self.preset)
        with self._failing_sink(), self.assertRaisesMessage(RuntimeError, "sink failed"):
            execute_workspace_task.call(run.pk)
        run.refresh_from_db()
        self.assertEqual(run.status, WorkspaceTaskRun.Status.FAILED)
        self.assertEqual(run.progress, 100)
        self.assertIsNotNone(run.completed_at)
        self.assertIn("RuntimeError: sink failed", run.metadata["command"]["error"])
        self.assertIn("Executing", run.log)
        self.assertIn("RuntimeError: sink failed", run.log)
        self.assertNotIn("exit_code", run.metadata["command"])
        self.assert_fully_cleaned_up()

    def test_failure_to_record_does_not_replace_the_original_error(self) -> None:
        run = WorkspaceTaskRun.objects.create(workspace=self.workspace_record, preset=self.preset)
        database_down = mock.patch(
            "django.db.models.query.QuerySet.update", side_effect=OSError("db down")
        )
        self.addCleanup(database_down.stop)

        def sink(instance, message):
            if message == "ready":
                database_down.start()
                raise RuntimeError("sink failed")

        with (
            mock.patch.object(WorkspaceTaskRun, "append_log", autospec=True, side_effect=sink),
            self.assertLogs("djdesk.inspector.tasks", level="ERROR"),
            self.assertRaisesMessage(RuntimeError, "sink failed"),
        ):
            execute_workspace_task.call(run.pk)
        database_down.stop()
        run.refresh_from_db()
        self.assertEqual(run.status, WorkspaceTaskRun.Status.RUNNING)
        self.assert_fully_cleaned_up()

    def test_buffered_output_survives_a_failing_log_save(self) -> None:
        self.write_manage("print('first')\nprint('second', flush=True)\ntime.sleep(120)\n")
        run = WorkspaceTaskRun.objects.create(workspace=self.workspace_record, preset=self.preset)
        real_append = WorkspaceTaskRun.append_log

        def sink(instance, message):
            if message == "second":
                raise OSError("disk full")
            return real_append(instance, message)

        with (
            mock.patch.object(WorkspaceTaskRun, "append_log", autospec=True, side_effect=sink),
            self.assertRaisesMessage(OSError, "disk full"),
        ):
            execute_workspace_task.call(run.pk)
        run.refresh_from_db()
        self.assertEqual(run.status, WorkspaceTaskRun.Status.FAILED)
        self.assertIn("first", run.log)
        self.assertIn("OSError: disk full", run.log)

    def test_unexpected_error_after_the_outcome_is_stored_keeps_that_outcome(self) -> None:
        self.write_manage("print('ok')\n")
        run = WorkspaceTaskRun.objects.create(workspace=self.workspace_record, preset=self.preset)
        with (
            mock.patch(
                "djdesk.inspector.tasks.transaction.on_commit", side_effect=RuntimeError("late")
            ),
            self.assertRaisesMessage(RuntimeError, "late"),
        ):
            execute_workspace_task.call(run.pk)
        run.refresh_from_db()
        self.assertEqual(run.status, WorkspaceTaskRun.Status.SUCCEEDED)
        self.assertEqual(run.metadata["command"]["exit_code"], 0)


@override_settings(INSPECTOR_COMMAND_OUTPUT_BYTES=8192)
class OutputBudgetTests(ProcessFixtureCase):
    def delivered_bytes(self) -> int:
        return sum(len(line.encode()) + 1 for line in self.lines[:-1])

    def test_newline_flood_and_single_huge_line_stop_at_the_budget(self) -> None:
        for name, write in (
            ("newline flood", "sys.stdout.buffer.write(b'x\\n' * 2_000_000)"),
            ("single line", "sys.stdout.buffer.write(b'x' * 4_000_000)"),
            ("endless", "while True: sys.stdout.buffer.write(b'y' * 1000 + b'\\n')"),
        ):
            with self.subTest(name):
                self.spawned.clear()
                (self.workspace / "pids").unlink(missing_ok=True)
                self.write_manage(
                    "child = subprocess.Popen(SLEEPER)\n"
                    "record(child.pid)\n"
                    f"{write}\n"
                    "sys.stdout.flush()\n"
                    "time.sleep(120)\n"
                )
                started = time.monotonic()
                result = self.run_fixture(timeout=30)
                self.assertLess(time.monotonic() - started, 15)
                self.assertTrue(result.output_limited)
                self.assertFalse(result.timed_out)
                self.assertLessEqual(self.delivered_bytes(), 8192 + 1)
                self.assertGreaterEqual(self.delivered_bytes(), 8192)
                self.assertLessEqual(max(len(line) for line in self.lines), 8192)
                self.assertIn("Output truncated: byte budget (8192)", self.lines[-1])
                self.assertEqual(len(self._recorded_pids()), 2)
                self.assert_fully_cleaned_up()

    def test_output_that_exactly_fills_the_budget_is_not_truncated(self) -> None:
        self.write_manage("sys.stdout.buffer.write(b'z' * 8191 + b'\\n')\n")
        result = self.run_fixture(timeout=30)
        self.assertEqual((result.exit_code, result.output_limited), (0, False))
        self.assertEqual(self.lines, ["z" * 8191])

    def test_multibyte_output_cut_at_the_budget_stays_decodable(self) -> None:
        self.write_manage("sys.stdout.buffer.write('é'.encode() * 100_000)\ntime.sleep(120)\n")
        with mock.patch.object(command_runner, "_output_encoding", return_value="utf-8"):
            result = self.run_fixture(timeout=30)
        self.assertTrue(result.output_limited)
        self.assertLessEqual(len(self.lines[0].encode()), 8192 + 3)

    def test_non_positive_budget_refuses_to_start(self) -> None:
        self.write_manage("print('never')\n")
        with override_settings(INSPECTOR_COMMAND_OUTPUT_BYTES=0):
            with self.assertRaisesMessage(command_runner.CommandExecutionError, "positive"):
                self.run_fixture()
        self.assertEqual(self.spawned, [])


class TaskOutputBudgetTests(ProcessFixtureCase, TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.workspace_record = Workspace.objects.create(
            name="Fixture",
            project_path=str(self.workspace),
            metadata={"recent_activity": [], "schema": {"nodes": []}},
        )
        self.preset = TaskPreset.objects.create(
            key="fixture-check", label="Fixture", description="Fixture", command=self.command
        )
        self.run_record = WorkspaceTaskRun.objects.create(
            workspace=self.workspace_record, preset=self.preset
        )

    def execute(self) -> dict:
        payload = execute_workspace_task.call(self.run_record.pk)
        self.run_record.refresh_from_db()
        return payload

    @override_settings(INSPECTOR_COMMAND_OUTPUT_BYTES=4096)
    def test_command_exceeding_the_output_budget_fails_the_run(self) -> None:
        self.write_manage("while True: print('x' * 99, flush=True)\n")
        payload = self.execute()
        self.assertEqual(self.run_record.status, WorkspaceTaskRun.Status.FAILED)
        self.assertTrue(payload["output_limited"])
        self.assertFalse(payload["timed_out"])
        self.assertIn("exceeded the byte budget", self.run_record.log)
        self.assertLess(len(self.run_record.log.encode()), 4096 * 2)
        self.assert_fully_cleaned_up()

    @override_settings(INSPECTOR_TASK_LOG_BYTES=2048)
    def test_exhausting_the_retained_log_stops_the_command_and_fails_the_run(self) -> None:
        self.write_manage(
            "child = subprocess.Popen(SLEEPER)\n"
            "record(child.pid)\n"
            "for _ in range(1000): print('tiny', flush=True)\n"
            "time.sleep(120)\n"
        )
        started = time.monotonic()
        payload = self.execute()
        self.assertLess(time.monotonic() - started, 15)
        self.assertEqual(self.run_record.status, WorkspaceTaskRun.Status.FAILED)
        self.assertTrue(payload["output_limited"])
        self.assertIn("retained log byte budget exhausted", payload["error"])
        self.assertLessEqual(len(self.run_record.log.encode()), 2048)
        self.assertTrue(self.run_record.log.endswith(WorkspaceTaskRun.LOG_TRUNCATION_MARKER))
        self.assertEqual(len(self._recorded_pids()), 2)
        self.assert_fully_cleaned_up()

    def test_exit_zero_with_a_truncated_log_is_not_a_success(self) -> None:
        self.write_manage("print('ok' + '.' * 100)\n")
        used = len(f"[00:00:00] Executing `{self.command}` inside {self.workspace}".encode())
        # Room for the command's one line but not for the closing status line.
        budget = used + 1 + len("[00:00:00] ok") + 100
        with override_settings(INSPECTOR_TASK_LOG_BYTES=budget):
            payload = self.execute()
        self.assertEqual(payload["exit_code"], 0)
        self.assertTrue(payload["output_limited"])
        self.assertEqual(self.run_record.status, WorkspaceTaskRun.Status.FAILED)
        self.assertIn("] ok", self.run_record.log)
        self.assertLessEqual(len(self.run_record.log.encode()), budget)
        self.assertTrue(self.run_record.log.endswith(WorkspaceTaskRun.LOG_TRUNCATION_MARKER))

    def test_complete_log_that_fits_the_budget_exactly_succeeds(self) -> None:
        self.write_manage("print('ok')\n")
        used = len(f"[00:00:00] Executing `{self.command}` inside {self.workspace}".encode())
        closing = len("[00:00:00] Command finished with exit code 0.")
        budget = used + 1 + len("[00:00:00] ok") + 1 + closing
        with override_settings(INSPECTOR_TASK_LOG_BYTES=budget):
            payload = self.execute()
        self.assertFalse(payload["output_limited"])
        self.assertEqual(self.run_record.status, WorkspaceTaskRun.Status.SUCCEEDED)
        self.assertEqual(len(self.run_record.log.encode()), budget)
        self.assertNotIn(WorkspaceTaskRun.LOG_TRUNCATION_MARKER, self.run_record.log)

    # Smaller than the first log entry whatever the interpreter and workspace paths are.
    @override_settings(INSPECTOR_TASK_LOG_BYTES=40)
    def test_command_is_not_started_when_the_first_log_entry_is_truncated(self) -> None:
        self.write_manage("record(os.getpid())\ntime.sleep(120)\n")
        self.assertGreater(len("[00:00:00] Executing `manage.py check` inside "), 40)
        started = time.monotonic()
        with override_settings(INSPECTOR_TASK_TIMEOUT=5):
            payload = self.execute()
        self.assertLess(time.monotonic() - started, 4)
        self.assertEqual(self.spawned, [])
        self.assertEqual(self._recorded_pids(), [])
        self.assertEqual(self.run_record.status, WorkspaceTaskRun.Status.FAILED)
        self.assertTrue(payload["output_limited"])
        self.assertIn("Command not started", payload["error"])
        self.assertNotIn("exit_code", payload)
        self.assertLessEqual(len(self.run_record.log.encode()), 40)

    def _fail_logging_with(self, error: BaseException):
        real_append = WorkspaceTaskRun.append_log

        def sink(instance, message):
            if message == "ready":
                raise error
            return real_append(instance, message)

        return mock.patch.object(WorkspaceTaskRun, "append_log", autospec=True, side_effect=sink)

    def _write_nearly_full_log_fixture(self, budget: int) -> None:
        """Print one line that leaves 20 bytes of the log budget, then ``ready``."""
        used = len(f"[00:00:00] Executing `{self.command}` inside {self.workspace}".encode())
        filler = budget - 20 - used - 1 - len("[00:00:00] ")
        self.assertGreater(filler, 0)
        self.write_manage(
            f"print('p' * {filler}, flush=True)\nprint('ready', flush=True)\ntime.sleep(120)\n"
        )

    @override_settings(INSPECTOR_TASK_LOG_BYTES=1024)
    def test_unexpected_failure_log_stays_within_the_log_budget(self) -> None:
        self._write_nearly_full_log_fixture(1024)
        with (
            self._fail_logging_with(RuntimeError("e" * 5000)),
            self.assertRaises(RuntimeError),
        ):
            self.execute()
        self.run_record.refresh_from_db()
        self.assertEqual(self.run_record.status, WorkspaceTaskRun.Status.FAILED)
        self.assertLessEqual(len(self.run_record.log.encode()), 1024)
        self.assertIn("failed unexpectedly: RuntimeError: eee", self.run_record.log)
        self.assertTrue(self.run_record.log.endswith(WorkspaceTaskRun.LOG_TRUNCATION_MARKER))

    @override_settings(INSPECTOR_TASK_LOG_BYTES=1024)
    def test_unexpected_failure_keeps_the_error_and_truncates_earlier_output(self) -> None:
        self._write_nearly_full_log_fixture(1024)
        with (
            self._fail_logging_with(RuntimeError("sink failed")),
            self.assertRaisesMessage(RuntimeError, "sink failed"),
        ):
            self.execute()
        self.run_record.refresh_from_db()
        self.assertEqual(self.run_record.status, WorkspaceTaskRun.Status.FAILED)
        self.assertLessEqual(len(self.run_record.log.encode()), 1024)
        self.assertGreater(len(self.run_record.log.encode()), 1024 - 8)
        self.assertTrue(self.run_record.log.endswith("RuntimeError: sink failed"))
        self.assertIn(WorkspaceTaskRun.LOG_TRUNCATION_MARKER, self.run_record.log)
        self.assertIn("Executing", self.run_record.log)

    def test_unexpected_error_text_is_bounded(self) -> None:
        self.write_manage("print('ready', flush=True)\ntime.sleep(120)\n")
        real_append = WorkspaceTaskRun.append_log

        def sink(instance, message):
            if message == "ready":
                raise RuntimeError("e" * 5_000_000)
            return real_append(instance, message)

        with (
            mock.patch.object(WorkspaceTaskRun, "append_log", autospec=True, side_effect=sink),
            self.assertRaises(RuntimeError),
        ):
            self.execute()
        self.run_record.refresh_from_db()
        self.assertEqual(self.run_record.status, WorkspaceTaskRun.Status.FAILED)
        self.assertLess(len(self.run_record.log), 4000)
        self.assertLess(len(self.run_record.metadata["command"]["error"]), 4000)
