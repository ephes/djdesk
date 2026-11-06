import subprocess
import tomllib
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from scripts.audit_dependencies import main

ROOT = Path(__file__).resolve().parents[1]


class DependencyAuditTests(TestCase):
    def test_runtime_floors_and_lock(self):
        project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
        self.assertIn("django>=5.2.19,<5.3", project["dependencies"])
        self.assertIn("sqlparse>=0.6.0", project["dependencies"])
        packages = tomllib.loads((ROOT / "uv.lock").read_text())["package"]
        versions = {
            p["name"]: tuple(map(int, p["version"].split(".")))
            for p in packages
            if p["name"] in {"django", "sqlparse"}
        }
        self.assertGreaterEqual(versions["django"], (5, 2, 19))
        self.assertGreaterEqual(versions["sqlparse"], (0, 6, 0))

    @patch("scripts.audit_dependencies.subprocess.run")
    def test_export_is_frozen_production_only_and_failure_propagates(self, run):
        run.side_effect = [None, subprocess.CalledProcessError(1, "pip-audit")]
        with self.assertRaises(subprocess.CalledProcessError):
            main()
        export, audit = [call.args[0] for call in run.call_args_list]
        self.assertIn("--frozen", export)
        self.assertIn("--no-dev", export)
        self.assertIn("--no-emit-project", export)
        self.assertNotIn("--extra", export)
        self.assertIn("--disable-pip", audit)
        self.assertTrue(all(call.kwargs["check"] for call in run.call_args_list))

    def test_ci_runs_audit_without_masking_errors(self):
        workflow = (ROOT / ".github/workflows/ci.yml").read_text()
        self.assertIn("--extra audit", workflow)
        self.assertIn("run: uv run --no-sync python scripts/audit_dependencies.py", workflow)
        self.assertNotIn("continue-on-error", workflow)
