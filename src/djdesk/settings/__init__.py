"""
Default settings module for ``DJANGO_SETTINGS_MODULE=djdesk.settings``.

This simply re-exports the local development settings. For alternative
environments, point ``DJANGO_SETTINGS_MODULE`` directly at
``djdesk.settings.local``, ``djdesk.settings.test``, or
``djdesk.settings.production``.
"""

from .local import *  # noqa: F401,F403
