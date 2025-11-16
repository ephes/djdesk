from __future__ import annotations

import os
import shlex
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Queue
from typing import Callable

from django.conf import settings


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


def validate_safe_command(command: str) -> str:
    """Ensure ``command`` belongs to ``INSPECTOR_SAFE_COMMANDS`` and return the prefix."""
    normalized = _normalize_command(command)
    for raw in settings.INSPECTOR_SAFE_COMMANDS:
        safe = _normalize_command(raw)
        if normalized == safe or normalized.startswith(f"{safe} "):
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

    safe_prefix = safe_prefix or validate_safe_command(command)
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
    reader.start()

    output_lines = 0
    deadline = time.monotonic() + timeout
    timed_out = False

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
        if process_done and queue.empty():
            break
        if not timed_out and time.monotonic() > deadline:
            timed_out = True
            log_callback(f"Timeout reached ({timeout}s). Terminating command.")
            process.kill()

    reader.join(timeout=0.5)

    if process.returncode is None:
        exit_code = process.wait()
    else:
        exit_code = process.returncode

    duration = time.monotonic() - start
    return CommandResult(
        exit_code=exit_code,
        duration=duration,
        output_lines=output_lines,
        safe_prefix=safe_prefix,
        timed_out=timed_out,
    )
