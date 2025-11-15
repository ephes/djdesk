"""
WSGI config for djdesk project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/4.0/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_ENV", "production")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "djdesk.settings")

application = get_wsgi_application()
