import importlib

from django.test import TestCase


class SettingsTests(TestCase):
    def test_admin_app_enabled(self) -> None:
        """Ensure the Django admin is part of the installed apps."""
        settings_module = importlib.import_module("djdesk.settings")
        self.assertIn(
            "django.contrib.admin",
            settings_module.INSTALLED_APPS,
            msg="django.contrib.admin should be enabled by default.",
        )
