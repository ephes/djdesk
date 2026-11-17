"""Fail the suite when models and committed migrations diverge.

``just test``, ``just ci`` and the manual quality workflow all run this module, so a
model change without its migration is caught before merge instead of at deploy time.
"""

import shutil
import sys
import tempfile
import uuid
from collections.abc import Callable
from io import StringIO
from pathlib import Path
from unittest import mock

from django.apps import apps
from django.conf import settings
from django.core.management import call_command
from django.test import TestCase, override_settings

PROJECT_APPS = ("inspector",)


def _run_drift_check() -> tuple[int, str]:
    """Run ``makemigrations --check --dry-run --noinput``; return (exit code, output).

    ``--noinput`` keeps rename candidates from prompting (which would hang a terminal
    run or raise ``EOFError`` without a TTY); they are reported as remove/add drift.
    """
    out = StringIO()
    try:
        call_command("makemigrations", "--check", "--dry-run", "--noinput", stdout=out, stderr=out)
    except SystemExit as exc:
        return int(exc.code or 0), out.getvalue()
    return 0, out.getvalue()


def _refuse_prompt(*args, **kwargs):
    raise AssertionError("the drift check must never prompt for input")


class MigrationDriftTests(TestCase):
    def test_models_match_committed_migrations(self) -> None:
        with mock.patch("builtins.input", _refuse_prompt):
            code, output = _run_drift_check()
        self.assertEqual(
            code,
            0,
            "Models and migrations diverge; run `uv run python manage.py makemigrations` "
            f"and commit the result:\n{output}",
        )
        self.assertIn("No changes detected", output)

    def test_project_migrations_are_not_disabled(self) -> None:
        """A ``None`` migration module would make the drift check vacuous for that app."""
        modules = getattr(settings, "MIGRATION_MODULES", {}) or {}
        for label in PROJECT_APPS:
            self.assertTrue(apps.is_installed(f"djdesk.{label}"), label)
            self.assertNotIn(label, modules, f"MIGRATION_MODULES overrides {label}")

    def test_check_detects_missing_migration(self) -> None:
        """Prove the gate is not vacuous: a history missing later migrations must fail."""
        root, package = self._probe_history()

        code, output = self._check_against(package)

        self.assertEqual(code, 1, output)
        self.assertIn("inspector", output)
        self.assertNotIn("No changes detected", output)
        self.assertEqual(
            sorted(p.name for p in (root / package).iterdir() if p.suffix == ".py"),
            ["0001_initial.py", "__init__.py"],
        )

    def test_rename_candidate_fails_without_prompting(self) -> None:
        """A renamed field would make Django ask interactively; the gate must just fail."""
        _, package = self._probe_history(
            lambda text: text.replace(
                '("docs_url", models.URLField', '("docs_link", models.URLField'
            )
        )

        code, output = self._check_against(package)

        self.assertEqual(code, 1, output)
        self.assertIn("docs_url", output)
        self.assertNotIn("Was ", output)

    def _check_against(self, package: str) -> tuple[int, str]:
        with (
            override_settings(MIGRATION_MODULES={"inspector": package}),
            mock.patch("builtins.input", _refuse_prompt),
        ):
            return _run_drift_check()

    def _probe_history(self, transform: Callable[[str], str] | None = None) -> tuple[Path, str]:
        """Create a throwaway migrations package holding only (a variant of) 0001_initial."""
        source = Path(apps.get_app_config("inspector").path) / "migrations" / "0001_initial.py"
        text = source.read_text(encoding="utf-8")
        if transform is not None:
            changed = transform(text)
            self.assertNotEqual(changed, text, "probe transform did not apply")
            text = changed
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        package = f"djdesk_drift_probe_{uuid.uuid4().hex}"
        (root / package).mkdir()
        (root / package / "__init__.py").write_text("", encoding="utf-8")
        (root / package / "0001_initial.py").write_text(text, encoding="utf-8")
        sys.path.insert(0, str(root))
        self.addCleanup(sys.path.remove, str(root))
        self.addCleanup(self._forget_modules, package)
        return root, package

    @staticmethod
    def _forget_modules(package: str) -> None:
        for name in [n for n in sys.modules if n == package or n.startswith(f"{package}.")]:
            del sys.modules[name]
