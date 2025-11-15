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
