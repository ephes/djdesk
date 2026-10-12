from __future__ import annotations

import os
import shlex
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Queue
from typing import Callable

from django.conf import settings
from django.core.management import load_command_class
from django.core.management.base import CommandError


class CommandExecutionError(Exception):
    """Raised when a workspace command cannot be executed."""


class UnsafeCommandError(CommandExecutionError):
    """Raised when a preset references a command outside the allowlist."""


@dataclass(slots=True)
class CommandResult:
    """Details returned after running a safe workspace command."""

    exit_code: int
    duration: float
    output_lines: int
    safe_prefix: str
    timed_out: bool = False


def _normalize_command(command: str) -> str:
    """Return a whitespace-normalized command for comparisons."""
    try:
        tokens = shlex.split(command)
    except ValueError as exc:  # unmatched quotes, etc.
        raise CommandExecutionError("Unable to parse command string.") from exc
    return " ".join(tokens)


_COMMON_OPTIONS = {"--verbosity", "-v", "--traceback", "--no-color", "--force-color"}
_COMMAND_OPTIONS = {
    "check": {"--tag", "-t", "--list-tags", "--deploy", "--fail-level", "--database"},
    "showmigrations": {"--database", "--list", "-l", "--plan", "-p"},
    "diffsettings": {"--all", "--output"},
    "sqlmigrate": {"--database", "--backwards"},
    "inspectdb": {"--database", "--include-partitions", "--include-views"},
    "dumpdata": {
        "--format",
        "--indent",
        "--database",
        "--exclude",
        "-e",
        "--natural-foreign",
        "--natural-primary",
        "--natural",
        "--all",
        "-a",
        "--pks",
    },
}


def validate_safe_command(command: str) -> str:
    """Validate token prefixes and the supported read-only core command options."""
    _normalize_command(command)
    args = shlex.split(command)
    for raw in settings.INSPECTOR_SAFE_COMMANDS:
        prefix = shlex.split(raw)
        if args[: len(prefix)] != prefix:
            continue
        safe = " ".join(prefix)
        if len(prefix) < 3 or Path(prefix[1]).name != "manage.py":
            if args != prefix:
                raise UnsafeCommandError("Custom allowlist entries require an exact argv match.")
            return safe
        name = prefix[2]
        if name not in _COMMAND_OPTIONS:
            raise UnsafeCommandError("Unsupported management command in allowlist.")
        allowed = _COMMON_OPTIONS | _COMMAND_OPTIONS[name]
        for value in args[3:]:
            if value.startswith("-"):
                option = value.split("=", 1)[0]
                if not option.startswith("--") and len(option) > 2:
                    option = option[:2]
                if option not in allowed:
                    raise UnsafeCommandError("Unsupported command arguments or file output.")
        try:
            parser = load_command_class("django.core", name).create_parser(args[0], name)
            parser.parse_args(args[3:])
        except (CommandError, SystemExit, ImportError) as exc:
            raise UnsafeCommandError("Unsupported command arguments.") from exc
        return safe
    raise UnsafeCommandError("Command is not part of INSPECTOR_SAFE_COMMANDS.")


def _resolve_workspace_path(raw_path: str) -> Path:
    """Validate the on-disk workspace directory and ensure manage.py exists."""
    candidate = Path(raw_path).expanduser()
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise CommandExecutionError("Workspace path does not exist.") from exc
    except PermissionError as exc:
        raise CommandExecutionError("Permission denied for workspace path.") from exc
    except OSError as exc:
        raise CommandExecutionError("Unable to inspect workspace path.") from exc

    if not resolved.is_dir():
        raise CommandExecutionError("Workspace path must be a directory.")
    manage_py = resolved / "manage.py"
    if not manage_py.exists():
        raise CommandExecutionError("manage.py not found in workspace path.")
    return resolved


def _stop_process_tree(process: subprocess.Popen) -> None:
    """Stop the session we own, including descendants holding stdout open."""
    if os.name == "nt":
        if process.poll() is None:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.kill()
    process.wait(timeout=5)


def run_command(
    *,
    command: str,
    workspace_path: str,
    timeout: float,
    log_callback: Callable[[str], None],
    safe_prefix: str | None = None,
) -> CommandResult:
    """
    Execute ``command`` inside ``workspace_path`` streaming output to ``log_callback``.
    """

    safe_prefix = validate_safe_command(command)
    workdir = _resolve_workspace_path(workspace_path)
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")

    try:
        args = shlex.split(command)
    except ValueError as exc:
        raise CommandExecutionError("Unable to parse command string.") from exc

    start = time.monotonic()
    try:
        process = subprocess.Popen(
            args,
            cwd=str(workdir),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
            start_new_session=(os.name != "nt"),
            creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0),
        )
    except OSError as exc:
        raise CommandExecutionError(f"Unable to start command: {exc}") from exc

    queue: Queue[str] = Queue()

    def _reader() -> None:
        assert process.stdout is not None
        for raw_line in process.stdout:
            queue.put(raw_line)
        process.stdout.close()

    reader = threading.Thread(target=_reader, daemon=True)
    output_lines = 0
    timed_out = False
    try:
        reader.start()
        deadline = time.monotonic() + timeout

        while True:
            remaining = deadline - time.monotonic()
            wait_time = 0.2
            if remaining < wait_time:
                wait_time = max(0.05, remaining)
            try:
                line = queue.get(timeout=max(wait_time, 0.05))
            except Empty:
                line = None
            if line is not None:
                cleaned = line.rstrip("\r\n")
                log_callback(cleaned)
                output_lines += 1
            process_done = process.poll() is not None
            if process_done and queue.empty() and not reader.is_alive():
                break
            if not timed_out and time.monotonic() > deadline:
                timed_out = True
                log_callback(f"Timeout reached ({timeout}s). Terminating command.")
                _stop_process_tree(process)

        exit_code = process.wait(timeout=2)
    finally:
        _stop_process_tree(process)
        reader.join(timeout=2)
        if process.stdout is not None and not reader.is_alive():
            process.stdout.close()

    duration = time.monotonic() - start
    return CommandResult(
        exit_code=exit_code,
        duration=duration,
        output_lines=output_lines,
        safe_prefix=safe_prefix,
        timed_out=timed_out,
    )
