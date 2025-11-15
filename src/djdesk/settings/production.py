"""Production-ready Django settings."""

import os

from django.core.exceptions import ImproperlyConfigured

from .base import *

DEBUG = False


def _require_env(key: str) -> str:
    value = os.environ.get(key)
    if not value:
        raise ImproperlyConfigured(f"The {key} environment variable must be set in production.")
    return value


SECRET_KEY = _require_env("DJANGO_SECRET_KEY")


def _env_list(key: str) -> list[str]:
    return [item.strip() for item in os.environ.get(key, "").split(",") if item.strip()]


ALLOWED_HOSTS = _env_list("DJANGO_ALLOWED_HOSTS")
CSRF_TRUSTED_ORIGINS = _env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

DATABASES["default"]["ENGINE"] = os.environ.get(
    "DJANGO_DB_ENGINE",
    DATABASES["default"]["ENGINE"],
)
DATABASES["default"]["NAME"] = os.environ.get(
    "DJANGO_DB_NAME",
    DATABASES["default"]["NAME"],
)
DATABASES["default"]["USER"] = os.environ.get("DJANGO_DB_USER", "")
DATABASES["default"]["PASSWORD"] = os.environ.get("DJANGO_DB_PASSWORD", "")
DATABASES["default"]["HOST"] = os.environ.get("DJANGO_DB_HOST", "")
DATABASES["default"]["PORT"] = os.environ.get("DJANGO_DB_PORT", "")
