"""
Environment-aware settings loader.

This allows ``DJANGO_SETTINGS_MODULE=djdesk.settings`` to resolve the correct
settings module based on the ``DJANGO_ENV`` environment variable while still
supporting explicit imports such as ``djdesk.settings.production``.
"""

import os
from importlib import import_module

ENVIRONMENT = os.environ.get("DJANGO_ENV", "local").lower()

MODULE_MAP = {
    "local": "djdesk.settings.local",
    "test": "djdesk.settings.test",
    "production": "djdesk.settings.production",
}

settings_module = import_module(MODULE_MAP.get(ENVIRONMENT, MODULE_MAP["local"]))

for setting in dir(settings_module):
    if setting.isupper():
        globals()[setting] = getattr(settings_module, setting)
