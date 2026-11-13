"""Opt-in local PostgreSQL integration checks against a disposable test database."""

import os

from .test import *

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("PGDATABASE", "postgres"),
        "USER": os.environ.get("PGUSER", ""),
        "HOST": os.environ.get("PGHOST", "localhost"),
        "PORT": os.environ.get("PGPORT", "5432"),
        "PASSWORD": os.environ.get("PGPASSWORD", ""),
    }
}
