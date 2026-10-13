from __future__ import annotations

import codecs
import locale
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Sequence

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
    output_limited: bool = False


def _pattern(expression: str) -> re.Pattern[str]:
    return re.compile(expression, re.ASCII)


# Permitted values never start with "-" and are never empty, so an accepted argv has exactly
# one reading: Django's argument parser cannot take a value for an option or the reverse.
_LABEL = r"[A-Za-z_][A-Za-z0-9_]*"
_APP_OR_MODEL = _pattern(rf"{_LABEL}(\.{_LABEL})?")
_IDENTIFIER = _pattern(r"[A-Za-z0-9_]+")
_ALIAS = _pattern(r"[A-Za-z0-9_][A-Za-z0-9_-]*")
_PRIMARY_KEYS = _pattern(r"[A-Za-z0-9_][A-Za-z0-9_-]*(,[A-Za-z0-9_][A-Za-z0-9_-]*)*")
_VERBOSITY = _pattern(r"[0-3]")

_COMMON_FLAGS = frozenset({"--traceback", "--no-color", "--force-color"})
_COMMON_VALUED = {"--verbosity": _VERBOSITY, "-v": _VERBOSITY}


@dataclass(frozen=True, slots=True)
class _CommandPolicy:
    """The complete set of argument shapes one management command may receive."""

    flags: frozenset[str] = frozenset()
    valued: Mapping[str, re.Pattern[str]] = field(default_factory=dict)
    positional: re.Pattern[str] | None = None
    max_positionals: int | None = None


# Every option is spelled out in full. Anything absent is refused: abbreviations, grouped or
# attached short options, "--", module-loading overrides (--settings, --pythonpath,
# diffsettings --default) and every option that writes a file (dumpdata --output / -o).
_COMMAND_POLICIES: dict[str, _CommandPolicy] = {
    "check": _CommandPolicy(
        flags=frozenset({"--list-tags", "--deploy"}),
        valued={
            "--tag": _IDENTIFIER,
            "-t": _IDENTIFIER,
            "--fail-level": _pattern(r"CRITICAL|ERROR|WARNING|INFO|DEBUG"),
            "--database": _ALIAS,
        },
        positional=_pattern(_LABEL),
    ),
    "showmigrations": _CommandPolicy(
        flags=frozenset({"--list", "-l", "--plan", "-p"}),
        valued={"--database": _ALIAS},
        positional=_pattern(_LABEL),
    ),
    "diffsettings": _CommandPolicy(
        flags=frozenset({"--all"}),
        # diffsettings --output selects a display format; it does not name a file.
        valued={"--output": _pattern(r"hash|unified")},
    ),
    "sqlmigrate": _CommandPolicy(
        flags=frozenset({"--backwards"}),
        valued={"--database": _ALIAS},
        positional=_IDENTIFIER,
        max_positionals=2,
    ),
    "inspectdb": _CommandPolicy(
        flags=frozenset({"--include-partitions", "--include-views"}),
        valued={"--database": _ALIAS},
        positional=_IDENTIFIER,
    ),
    "dumpdata": _CommandPolicy(
        flags=frozenset({"--natural-foreign", "--natural-primary", "--all", "-a"}),
        valued={
            "--format": _pattern(r"json|jsonl|xml|yaml"),
            "--indent": _pattern(r"[0-9]{1,2}"),
            "--database": _ALIAS,
            "--exclude": _APP_OR_MODEL,
            "-e": _APP_OR_MODEL,
            "--pks": _PRIMARY_KEYS,
        },
        positional=_APP_OR_MODEL,
    ),
}

_MANAGE_SCRIPT = "manage.py"


def split_command(command: str) -> list[str]:
    """Split a stored command line into the argv list that is validated and executed."""
    try:
        return shlex.split(command)
    except ValueError as exc:  # unmatched quotes, etc.
        raise CommandExecutionError("Unable to parse command string.") from exc


def _check_arguments(policy: _CommandPolicy, arguments: Sequence[str]) -> None:
    refused = UnsafeCommandError("Command arguments are not permitted for this command.")
    positionals = 0
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        index += 1
        if not argument.startswith("-"):
            positionals += 1
            if policy.positional is None or not policy.positional.fullmatch(argument):
                raise refused
            if policy.max_positionals is not None and positionals > policy.max_positionals:
                raise refused
            continue
        # Only long options may carry "=value"; short options must be the exact token.
        if argument.startswith("--"):
            name, has_value, value = argument.partition("=")
        else:
            name, has_value, value = argument, "", ""
        if name in _COMMON_FLAGS or name in policy.flags:
            if has_value:
                raise refused
            continue
        value_pattern = policy.valued.get(name) or _COMMON_VALUED.get(name)
        if value_pattern is None:
            raise refused
        if not has_value:
            if index >= len(arguments):
                raise refused
            value = arguments[index]
            index += 1
        if not value_pattern.fullmatch(value):
            raise refused


def validate_argv(argv: Sequence[str]) -> str:
    """
    Return the allowlisted prefix for ``argv`` or raise ``UnsafeCommandError``.

    This is the only command guard. It receives the argv list exactly as it is handed to the
    operating system and accepts it only when it is ``<interpreter> manage.py <command>`` from
    ``INSPECTOR_SAFE_COMMANDS`` followed solely by argument shapes that command's policy lists.
    """
    argv = list(argv)
    for raw in settings.INSPECTOR_SAFE_COMMANDS:
        prefix = split_command(raw)
        if len(prefix) == 3 and prefix[1] == _MANAGE_SCRIPT and argv[:3] == prefix:
            break
    else:
        raise UnsafeCommandError("Command is not part of INSPECTOR_SAFE_COMMANDS.")
    policy = _COMMAND_POLICIES.get(prefix[2])
    if policy is None:
        raise UnsafeCommandError("No argument policy exists for this management command.")
    _check_arguments(policy, argv[3:])
    return " ".join(prefix)


def validate_safe_command(command: str) -> str:
    """Validate a stored command line and return its allowlisted prefix."""
    return validate_argv(split_command(command))


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
    manage_py = resolved / _MANAGE_SCRIPT
    if not manage_py.exists():
        raise CommandExecutionError("manage.py not found in workspace path.")
    return resolved


_READ_SIZE = 65536
_POLL_INTERVAL = 0.02
# How long the command's process group gets to exit after SIGTERM before SIGKILL.
TERMINATION_GRACE_SECONDS = 2.0

if os.name == "nt":  # pragma: no cover - exercised only on Windows
    import _winapi
    import msvcrt

    _OWN_GROUP = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}

    def _prepare_stream(stream) -> None:
        pass

    def _read_available(stream, size: int = _READ_SIZE) -> bytes | None:
        try:
            available = _winapi.PeekNamedPipe(msvcrt.get_osfhandle(stream.fileno()), 0)[0]
        except BrokenPipeError:
            return b""
        if not available:
            return None
        return os.read(stream.fileno(), min(available, size, _READ_SIZE))

    def _leader_exited(process: subprocess.Popen) -> bool:
        # The open process handle keeps the pid reserved after the exit status is read.
        return process.poll() is not None

    def _stop_group(process: subprocess.Popen) -> None:
        # Windows has no group-wide SIGTERM; taskkill /T walks the tree below a live child.
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
        process.kill()

else:
    _OWN_GROUP = {"start_new_session": True}

    def _prepare_stream(stream) -> None:
        os.set_blocking(stream.fileno(), False)

    def _read_available(stream, size: int = _READ_SIZE) -> bytes | None:
        """Return pending output, ``b""`` at end of stream, or ``None`` when nothing is ready."""
        try:
            return os.read(stream.fileno(), min(size, _READ_SIZE))
        except BlockingIOError:
            return None

    def _leader_exited(process: subprocess.Popen) -> bool:
        """
        Report whether the direct child has exited, without reaping it.

        Until ``Popen.wait`` reaps the child its pid, and so the id of the process group it
        leads, cannot be reused; signalling the group therefore only ever reaches our command.
        """
        if process.returncode is not None:
            return True
        try:
            status = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        except ChildProcessError:
            return True
        return status is not None

    def _signal_group(process: subprocess.Popen, signum: int) -> None:
        try:
            os.killpg(process.pid, signum)
        except (ProcessLookupError, PermissionError):
            # Nothing left to signal (macOS reports EPERM for a group of zombies).
            pass

    def _stop_group(process: subprocess.Popen) -> None:
        _signal_group(process, signal.SIGTERM)
        deadline = time.monotonic() + TERMINATION_GRACE_SECONDS
        while time.monotonic() < deadline:
            # The group is gone once the child exited and no descendant holds the pipe open.
            if _read_available(process.stdout) == b"" and _leader_exited(process):
                break
            time.sleep(_POLL_INTERVAL)
        _signal_group(process, signal.SIGKILL)


def _stop_and_reap(process: subprocess.Popen) -> None:
    """Stop everything the command started, close its pipe and reap it. Runs on every exit."""
    try:
        # A reaped child no longer reserves its group id, so there is nothing safe to signal.
        if process.returncode is None:
            _stop_group(process)
    finally:
        try:
            process.stdout.close()
        finally:
            process.wait()


def _output_encoding() -> str:
    """Return the encoding a text-mode pipe assumes: UTF-8 in UTF-8 mode, else the locale's."""
    return "utf-8" if sys.flags.utf8_mode else locale.getencoding()


def run_command(
    *,
    command: str,
    workspace_path: str,
    timeout: float,
    log_callback: Callable[[str], None],
) -> CommandResult:
    """
    Execute ``command`` inside ``workspace_path`` streaming output to ``log_callback``.

    The command runs in its own process group. However this function exits (completion,
    timeout, output budget, or an exception from ``log_callback``) the whole group is
    stopped, the output pipe is closed and the child is reaped before control returns.

    At most ``INSPECTOR_COMMAND_OUTPUT_BYTES`` bytes of output are read, however they are
    split into lines; a command that produces more is stopped and reported as
    ``output_limited``. Nothing is queued, so the bytes held here never exceed that budget.
    """
    argv = split_command(command)
    safe_prefix = validate_argv(argv)
    workdir = _resolve_workspace_path(workspace_path)
    budget = settings.INSPECTOR_COMMAND_OUTPUT_BYTES
    if budget <= 0:
        raise CommandExecutionError("INSPECTOR_COMMAND_OUTPUT_BYTES must be positive.")
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")

    start = time.monotonic()
    deadline = start + timeout
    try:
        process = subprocess.Popen(
            argv,
            cwd=str(workdir),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
            env=env,
            **_OWN_GROUP,
        )
    except OSError as exc:
        raise CommandExecutionError(f"Unable to start command: {exc}") from exc

    # Undecodable bytes are replaced instead of aborting the run.
    decoder = codecs.getincrementaldecoder(_output_encoding())(errors="replace")
    pending = ""
    output_lines = 0
    timed_out = False
    output_limited = False
    received = 0
    try:
        _prepare_stream(process.stdout)
        end_of_output = False
        while not end_of_output:
            if time.monotonic() >= deadline:
                timed_out = True
                break
            # Ask for one byte more than the budget has left to tell "full" from "exceeded".
            chunk = _read_available(process.stdout, budget - received + 1)
            if chunk is None:
                time.sleep(_POLL_INTERVAL)
                continue
            output_limited = len(chunk) > budget - received
            end_of_output = chunk == b"" or output_limited
            chunk = chunk[: budget - received]
            received += len(chunk)
            pending += decoder.decode(chunk, final=end_of_output)
            *lines, pending = pending.split("\n")
            for line in lines:
                log_callback(line.rstrip("\r"))
                output_lines += 1
        if pending:
            log_callback(pending.rstrip("\r"))
            output_lines += 1
        # A command may close its output and keep running; the deadline still applies.
        while not (timed_out or output_limited or _leader_exited(process)):
            if time.monotonic() >= deadline:
                timed_out = True
            else:
                time.sleep(_POLL_INTERVAL)
        if timed_out:
            log_callback(f"Timeout reached ({timeout}s). Terminating command.")
        if output_limited:
            log_callback(
                f"Output truncated: byte budget ({budget}) exhausted. Terminating command."
            )
    finally:
        _stop_and_reap(process)

    return CommandResult(
        exit_code=process.returncode,
        duration=time.monotonic() - start,
        output_lines=output_lines,
        safe_prefix=safe_prefix,
        timed_out=timed_out,
        output_limited=output_limited,
    )
