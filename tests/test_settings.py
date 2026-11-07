import os
import runpy
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase


class SettingsTests(TestCase):
    def test_admin_app_enabled(self) -> None:
        """Ensure the Django admin is part of the installed apps."""
        self.assertIn(
            "django.contrib.admin",
            settings.INSTALLED_APPS,
            msg="django.contrib.admin should be enabled by default.",
        )


class DesktopStateTests(TestCase):
    def test_development_defaults_and_explicit_export_override(self):
        source = Path(__file__).resolve().parents[1] / "src/djdesk/settings/base.py"
        with patch.dict(os.environ, {}, clear=True):
            config = runpy.run_path(str(source))
        self.assertEqual(config["DATABASES"]["default"]["NAME"], source.parents[3] / "db.sqlite3")
        self.assertEqual(config["INSPECTOR_DATA_LAB_ROOT"], source.parents[3] / "var/data_lab")
        with TemporaryDirectory() as temporary:
            state = Path(temporary) / "state"
            exports = Path(temporary) / "exports"
            with patch.dict(
                os.environ,
                {
                    "DJDESK_STATE_ROOT": str(state),
                    "DJDESK_DATA_LAB_ROOT": str(exports),
                },
                clear=True,
            ):
                config = runpy.run_path(str(source))
            self.assertEqual(config["DATABASES"]["default"]["NAME"], state.resolve() / "db.sqlite3")
            self.assertEqual(config["INSPECTOR_DATA_LAB_ROOT"], exports)
            self.assertEqual(
                config["INSPECTOR_DOCS_BUNDLE_ROOT"], source.parents[3] / "var/docs_bundle"
            )

    def test_workspace_and_export_survive_replacing_packaged_sources(self):
        source = Path(__file__).resolve().parents[1] / "src/djdesk"
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = root / "django-bundle"
            state = root / "user data"

            def copy_sources():
                shutil.copytree(
                    source, bundle / "src/djdesk", ignore=shutil.ignore_patterns("__pycache__")
                )

            copy_sources()
            env = {
                key: value
                for key, value in os.environ.items()
                if not key.startswith(("DJANGO_", "DJDESK_", "PYTHON"))
            }
            env.update(
                DJDESK_STATE_ROOT=str(state),
                DJANGO_SETTINGS_MODULE="djdesk.settings.local",
                PYTHONPATH=str(bundle / "src"),
                PYTHONDONTWRITEBYTECODE="1",
            )
            common = """
import django
from pathlib import Path
from django.conf import settings
django.setup()
from djdesk.inspector.models import Workspace
from djdesk.inspector import data_lab
root = Path(settings.STATE_ROOT)
assert settings.DATABASES['default']['NAME'] == root / 'db.sqlite3'
assert settings.INSPECTOR_DATA_LAB_ROOT == root / 'var/data_lab'
"""
            create = (
                common
                + """
from django.core.management import call_command
call_command('migrate', verbosity=0)
workspace = Workspace.objects.create(
    name='Synthetic durable state', project_path='/synthetic/project')
notebook = data_lab.export_notebook(workspace, 'schema-audit')
assert notebook.is_relative_to(root)
"""
            )
            result = subprocess.run(
                [sys.executable, "-c", create],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            database = state / "db.sqlite3"
            notebook = next((state / "var/data_lab").rglob("*.ipynb"))
            saved = (database.read_bytes(), notebook.read_bytes())
            self.assertFalse((bundle / "db.sqlite3").exists())
            self.assertFalse((bundle / "var/data_lab").exists())
            shutil.rmtree(bundle)
            copy_sources()
            self.assertEqual(saved, (database.read_bytes(), notebook.read_bytes()))
            reopen = (
                common
                + """
workspace = Workspace.objects.get(name='Synthetic durable state')
assert data_lab.load_notebook(workspace, 'schema-audit')['cells']
"""
            )
            result = subprocess.run(
                [sys.executable, "-c", reopen],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
