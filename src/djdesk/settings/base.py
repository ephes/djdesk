"""Shared Django settings used by every environment."""

import os
from pathlib import Path

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parents[3]


# Quick-start development settings - unsuitable for production
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "django-insecure-CHANGE-ME")

# DEBUG is disabled by default; local/test modules override it explicitly.
DEBUG = False

ALLOWED_HOSTS: list[str] = []


# Application definition

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "django_tasks",
    "djdesk.inspector",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "djdesk.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "djdesk.wsgi.application"


# Database
# https://docs.djangoproject.com/en/4.0/ref/settings/#databases

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}


# Password validation
# https://docs.djangoproject.com/en/4.0/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]


# Internationalization
# https://docs.djangoproject.com/en/4.0/topics/i18n/

LANGUAGE_CODE = "en-us"

TIME_ZONE = "UTC"

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/4.0/howto/static-files/

STATIC_URL = "static/"
_STATIC_ROOT_OVERRIDE = os.environ.get("DJANGO_STATIC_ROOT")
if _STATIC_ROOT_OVERRIDE:
    STATIC_ROOT = Path(_STATIC_ROOT_OVERRIDE).expanduser().resolve()
else:
    STATIC_ROOT = BASE_DIR / "staticfiles"

# Default primary key field type
# https://docs.djangoproject.com/en/4.0/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


def _env_flag(env_name: str, default: bool) -> bool:
    """Return a boolean-ish environment variable with a friendly fallback."""
    raw_value = os.environ.get(env_name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}

INSPECTOR_DOCS_BASE_URL = os.environ.get(
    "DJDESK_DOCS_BASE_URL", "https://djdesk.readthedocs.io/en/latest"
)

INSPECTOR_SAFE_COMMANDS = [
    "python manage.py showmigrations",
    "python manage.py check",
    "python manage.py diffsettings",
    "python manage.py sqlmigrate",
    "python manage.py inspectdb",
    "python manage.py dumpdata",
]
INSPECTOR_TASK_TIMEOUT = int(os.environ.get("DJDESK_INSPECTOR_TASK_TIMEOUT", "60"))

INSPECTOR_DATA_LAB_ROOT = Path(
    os.environ.get("DJDESK_DATA_LAB_ROOT", BASE_DIR / "var" / "data_lab")
).expanduser()
INSPECTOR_DATA_LAB_LIVE = _env_flag("DJDESK_FLAG_DATA_LAB_LIVE", False)
