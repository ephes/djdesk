"""Guard the shape of the GitHub Actions workflows.

The checks are text based on purpose: the runtime dependencies do not include a YAML parser
and the workflows are small enough that these assertions stay readable.
"""

import re
from pathlib import Path
from unittest import TestCase

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"


def _read(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def _top_level_block(text: str, key: str) -> str:
    """Return the body of a top-level YAML mapping key (up to the next top-level key)."""
    match = re.search(rf"^{re.escape(key)}:[^\n]*\n((?:[ \t]+[^\n]*\n|\n)*)", text, re.MULTILINE)
    if match is None:
        raise AssertionError(f"top-level key {key!r} not found")
    return match.group(1)


class QualityWorkflowTests(TestCase):
    def setUp(self) -> None:
        self.text = _read("ci.yml")

    def test_runs_on_push_and_pull_request(self) -> None:
        triggers = _top_level_block(self.text, "on")
        self.assertRegex(triggers, r"(?m)^  push:")
        self.assertRegex(triggers, r"(?m)^  pull_request:")
        self.assertNotIn("pull_request_target", self.text)

    def test_token_is_read_only(self) -> None:
        permissions = _top_level_block(self.text, "permissions")
        self.assertEqual(permissions.strip(), "contents: read")
        self.assertEqual(len(re.findall(r"(?m)^\s*permissions:", self.text)), 1)
        self.assertNotIn(": write", self.text)

    def test_uses_no_secrets_or_uploads(self) -> None:
        self.assertNotIn("secrets.", self.text)
        self.assertNotIn("upload-artifact", self.text)
        self.assertNotIn("electron-builder", self.text)
        self.assertNotIn("npm run build", self.text)
        self.assertEqual(
            len(re.findall(r"persist-credentials: false", self.text)),
            len(re.findall(r"uses: actions/checkout@", self.text)),
        )

    def test_installs_from_committed_lock(self) -> None:
        self.assertIn("uv sync --locked --extra dev", self.text)

    def test_runs_ruff_and_django_tests_with_test_settings(self) -> None:
        self.assertIn("DJANGO_SETTINGS_MODULE: djdesk.settings.test", self.text)
        self.assertRegex(self.text, r"run: uv run --no-sync ruff check \.")
        self.assertRegex(self.text, r"run: uv run --no-sync python manage\.py test tests")

    def test_runs_electron_guard_tests(self) -> None:
        self.assertRegex(self.text, r"run: npm test\n\s+working-directory: electron")

    def test_steps_do_not_mask_failures(self) -> None:
        self.assertNotIn("continue-on-error", self.text)
        self.assertNotIn("|| true", self.text)


class PackagingWorkflowTests(TestCase):
    def test_packaging_stays_manual(self) -> None:
        triggers = _top_level_block(_read("electron-desktop.yml"), "on")
        self.assertEqual(triggers.strip(), "workflow_dispatch:")
