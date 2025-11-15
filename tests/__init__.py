import os
import pathlib
import sys

_PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SRC = _PROJECT_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

os.environ.setdefault("DJANGO_ENV", "test")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "djdesk.settings.test")

import django

django.setup()
