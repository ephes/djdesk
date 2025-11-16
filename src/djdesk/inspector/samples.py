from __future__ import annotations

import shutil
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .assets import sample_root

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]


@dataclass(frozen=True, slots=True)
class SampleProject:
    slug: str
    title: str
    description: str
    project_name: str
    files: dict[str, str]


def _render_manage_py(project_name: str) -> str:
    return textwrap.dedent(
        f"""\
        #!/usr/bin/env python3
        import os
        import sys


        def main() -> None:
            os.environ.setdefault("DJANGO_SETTINGS_MODULE", "{project_name}.settings")
            from django.core.management import execute_from_command_line

            execute_from_command_line(sys.argv)


        if __name__ == "__main__":
            main()
        """
    )


def _render_settings(project_name: str, *, slug: str, extra_apps: Iterable[str]) -> str:
    installed = ",\n    ".join(f'"{app}"' for app in [*DJANGO_APPS, *extra_apps])
    return textwrap.dedent(
        f"""\
        from pathlib import Path

        BASE_DIR = Path(__file__).resolve().parent.parent

        SECRET_KEY = "djdesk-sample-{slug}"

        DEBUG = True

        ALLOWED_HOSTS: list[str] = []

        INSTALLED_APPS = [
            {installed}
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

        ROOT_URLCONF = "{project_name}.urls"

        TEMPLATES = [
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [BASE_DIR / "templates"],
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

        WSGI_APPLICATION = "{project_name}.wsgi.application"

        DATABASES = {{
            "default": {{
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": BASE_DIR / "db.sqlite3",
            }}
        }}

        AUTH_PASSWORD_VALIDATORS = [
            {{"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"}},
            {{"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"}},
            {{"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"}},
            {{"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"}},
        ]

        LANGUAGE_CODE = "en-us"
        TIME_ZONE = "UTC"
        USE_I18N = True
        USE_TZ = True

        STATIC_URL = "static/"

        DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
        """
    )


def _render_urls(url_includes: Iterable[tuple[str, str]]) -> str:
    includes = list(url_includes)
    include_import = ", include" if includes else ""
    blocks = ["    path('admin/', admin.site.urls),"]
    for module, prefix in includes:
        route = prefix or ""
        blocks.append(f"    path('{route}', include('{module}')),")
    patterns = "\n".join(blocks)
    return textwrap.dedent(
        f"""\
        from django.contrib import admin
        from django.urls import path{include_import}


        urlpatterns = [
        {patterns}
        ]
        """
    )


def _render_asgi(project_name: str) -> str:
    return textwrap.dedent(
        f"""\
        import os

        from django.core.asgi import get_asgi_application

        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "{project_name}.settings")

        application = get_asgi_application()
        """
    )


def _render_wsgi(project_name: str) -> str:
    return textwrap.dedent(
        f"""\
        import os

        from django.core.wsgi import get_wsgi_application

        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "{project_name}.settings")

        application = get_wsgi_application()
        """
    )


def _base_project_files(
    *,
    project_name: str,
    slug: str,
    app_labels: Iterable[str],
    url_includes: Iterable[tuple[str, str]],
) -> dict[str, str]:
    files = {
        "manage.py": _render_manage_py(project_name),
        f"{project_name}/__init__.py": "",
        f"{project_name}/settings.py": _render_settings(
            project_name, slug=slug, extra_apps=app_labels
        ),
        f"{project_name}/urls.py": _render_urls(url_includes),
        f"{project_name}/wsgi.py": _render_wsgi(project_name),
        f"{project_name}/asgi.py": _render_asgi(project_name),
    }
    return files


def _polls_bundle() -> SampleProject:
    project_name = "pollsite"
    slug = "django-polls"
    files = _base_project_files(
        project_name=project_name,
        slug=slug,
        app_labels=["polls"],
        url_includes=[("polls.urls", "polls/")],
    )
    files.update(
        {
            "README.md": textwrap.dedent(
                """\
                # Django Polls (tutorial)

                Minimal polls app mirroring the official Django tutorial. Use it to
                populate schema graphs and safe command demos inside DJDesk.
                """
            ),
            "polls/__init__.py": "",
            "polls/apps.py": textwrap.dedent(
                """\
                from django.apps import AppConfig


                class PollsConfig(AppConfig):
                    default_auto_field = "django.db.models.BigAutoField"
                    name = "polls"
                """
            ),
            "polls/models.py": textwrap.dedent(
                """\
                from django.db import models


                class Question(models.Model):
                    question_text = models.CharField(max_length=200)
                    pub_date = models.DateTimeField("date published")

                    def __str__(self) -> str:  # pragma: no cover - sample helper
                        return self.question_text


                class Choice(models.Model):
                    question = models.ForeignKey(
                        Question, related_name="choices", on_delete=models.CASCADE
                    )
                    choice_text = models.CharField(max_length=200)
                    votes = models.IntegerField(default=0)

                    def __str__(self) -> str:  # pragma: no cover - sample helper
                        return self.choice_text
                """
            ),
            "polls/admin.py": textwrap.dedent(
                """\
                from django.contrib import admin

                from .models import Choice, Question


                @admin.register(Question)
                class QuestionAdmin(admin.ModelAdmin):
                    list_display = ("question_text", "pub_date")
                    search_fields = ("question_text",)


                @admin.register(Choice)
                class ChoiceAdmin(admin.ModelAdmin):
                    list_display = ("choice_text", "question", "votes")
                    list_filter = ("question",)
                """
            ),
            "polls/views.py": textwrap.dedent(
                """\
                from django.shortcuts import render

                from .models import Question


                def index(request):
                    questions = Question.objects.order_by("-pub_date")[:5]
                    return render(
                        request,
                        "polls/index.html",
                        {"question_list": questions},
                    )
                """
            ),
            "polls/urls.py": textwrap.dedent(
                """\
                from django.urls import path

                from . import views

                app_name = "polls"

                urlpatterns = [
                    path("", views.index, name="index"),
                ]
                """
            ),
            "polls/templates/polls/index.html": textwrap.dedent(
                """\
                {% extends "polls/layout.html" %}

                {% block content %}
                <h1>Polls</h1>
                <ul>
                {% for question in question_list %}
                    <li>{{ question.question_text }}</li>
                {% empty %}
                    <li>No polls are available.</li>
                {% endfor %}
                </ul>
                {% endblock %}
                """
            ),
            "polls/templates/polls/layout.html": textwrap.dedent(
                """\
                <!doctype html>
                <html>
                <head>
                    <meta charset="utf-8">
                    <title>Django Polls</title>
                </head>
                <body>
                    {% block content %}{% endblock %}
                </body>
                </html>
                """
            ),
            "polls/migrations/__init__.py": "",
        }
    )
    return SampleProject(
        slug=slug,
        title="Django Polls",
        description="Official tutorial sample preserved for schema demos.",
        project_name=project_name,
        files=files,
    )


def _djdesk_bundle() -> SampleProject:
    project_name = "atlasstudio"
    slug = "djdesk"
    files = _base_project_files(
        project_name=project_name,
        slug=slug,
        app_labels=["catalog", "alerts", "ledger"],
        url_includes=[
            ("catalog.urls", "catalog/"),
            ("alerts.urls", "alerts/"),
            ("ledger.urls", "ledger/"),
        ],
    )
    files["README.md"] = textwrap.dedent(
        """\
        # Atlas Telemetry Studio

        Dogfood workspace that mirrors the DJDesk tutorial narrative. It carries
        fake schema + task data so screenshots stay representative.
        """
    )
    for app_name, model_body, view_body in [
        (
            "catalog",
            textwrap.dedent(
                """\
                from django.db import models


                class Dataset(models.Model):
                    name = models.CharField(max_length=120)
                    owner = models.CharField(max_length=60)
                    rows = models.IntegerField(default=0)
                    refreshed_at = models.DateTimeField(null=True, blank=True)

                    def __str__(self) -> str:  # pragma: no cover - sample helper
                        return self.name
                """
            ),
            textwrap.dedent(
                """\
                from django.http import JsonResponse

                from .models import Dataset


                def overview(request):
                    payload = [
                        {"name": ds.name, "owner": ds.owner, "rows": ds.rows}
                        for ds in Dataset.objects.all()
                    ]
                    return JsonResponse({"datasets": payload})
                """
            ),
        ),
        (
            "alerts",
            textwrap.dedent(
                """\
                from django.db import models


                class Monitor(models.Model):
                    slug = models.SlugField(unique=True)
                    description = models.TextField()
                    severity = models.CharField(max_length=20, default="info")
                    active = models.BooleanField(default=True)

                    def __str__(self) -> str:  # pragma: no cover - sample helper
                        return self.slug
                """
            ),
            textwrap.dedent(
                """\
                from django.http import JsonResponse

                from .models import Monitor


                def overview(request):
                    payload = [
                        {
                            "slug": monitor.slug,
                            "severity": monitor.severity,
                            "active": monitor.active,
                        }
                        for monitor in Monitor.objects.all()
                    ]
                    return JsonResponse({"alerts": payload})
                """
            ),
        ),
        (
            "ledger",
            textwrap.dedent(
                """\
                from django.db import models


                class Entry(models.Model):
                    reference = models.CharField(max_length=60)
                    amount = models.DecimalField(max_digits=12, decimal_places=2)
                    direction = models.CharField(max_length=10, default="credit")
                    created_at = models.DateTimeField(auto_now_add=True)

                    def __str__(self) -> str:  # pragma: no cover - sample helper
                        return f"{self.reference} ({self.amount})"
                """
            ),
            textwrap.dedent(
                """\
                from django.http import JsonResponse

                from .models import Entry


                def overview(request):
                    payload = [
                        {"reference": entry.reference, "amount": float(entry.amount)}
                        for entry in Entry.objects.all()
                    ]
                    return JsonResponse({"entries": payload})
                """
            ),
        ),
    ]:
        files.update(
            {
                f"{app_name}/__init__.py": "",
                f"{app_name}/apps.py": textwrap.dedent(
                    f"""\
                    from django.apps import AppConfig


                    class {app_name.title()}Config(AppConfig):
                        default_auto_field = "django.db.models.BigAutoField"
                        name = "{app_name}"
                    """
                ),
                f"{app_name}/models.py": model_body,
                f"{app_name}/views.py": view_body,
                f"{app_name}/urls.py": textwrap.dedent(
                    f"""\
                    from django.urls import path

                    from . import views

                    app_name = "{app_name}"

                    urlpatterns = [
                        path("", views.overview, name="overview"),
                    ]
                    """
                ),
                f"{app_name}/migrations/__init__.py": "",
            }
        )
    return SampleProject(
        slug=slug,
        title="Atlas Telemetry Studio",
        description="Dogfood workspace with catalog/alerts/ledger apps.",
        project_name=project_name,
        files=files,
    )


def _generated_bundle() -> SampleProject:
    project_name = "generatedsuite"
    slug = "generated"
    files = _base_project_files(
        project_name=project_name,
        slug=slug,
        app_labels=["alpha", "beta", "gamma"],
        url_includes=[
            ("alpha.urls", "alpha/"),
            ("beta.urls", "beta/"),
            ("gamma.urls", "gamma/"),
        ],
    )
    files["README.md"] = textwrap.dedent(
        """\
        # Auto-generated sample

        Synthetic project that stress-tests schema graphs with multiple
        interdependent apps. Use it to demo relationship-heavy diagrams.
        """
    )
    relationships = [
        (
            "alpha",
            textwrap.dedent(
                """\
                from django.db import models


                class ResearchNode(models.Model):
                    slug = models.SlugField(unique=True)
                    headline = models.CharField(max_length=140)
                    score = models.FloatField(default=0.0)

                    def __str__(self) -> str:  # pragma: no cover - sample helper
                        return self.slug
                """
            ),
            "alpha",
        ),
        (
            "beta",
            textwrap.dedent(
                """\
                from django.db import models

                from alpha.models import ResearchNode


                class BetaLink(models.Model):
                    source = models.ForeignKey(
                        ResearchNode, related_name="beta_links", on_delete=models.CASCADE
                    )
                    label = models.CharField(max_length=80)
                    weight = models.IntegerField(default=1)

                    def __str__(self) -> str:  # pragma: no cover - sample helper
                        return f"{self.source.slug}:{self.label}"
                """
            ),
            "beta",
        ),
        (
            "gamma",
            textwrap.dedent(
                """\
                from django.db import models

                from beta.models import BetaLink


                class GammaSnapshot(models.Model):
                    link = models.ForeignKey(
                        BetaLink, related_name="snapshots", on_delete=models.CASCADE
                    )
                    notes = models.TextField()
                    observed_at = models.DateTimeField(auto_now_add=True)

                    def __str__(self) -> str:  # pragma: no cover - sample helper
                        return f"snapshot-{self.pk}"
                """
            ),
            "gamma",
        ),
    ]
    for app_name, model_body, class_prefix in relationships:
        files.update(
            {
                f"{app_name}/__init__.py": "",
                f"{app_name}/apps.py": textwrap.dedent(
                    f"""\
                    from django.apps import AppConfig


                    class {class_prefix.title()}Config(AppConfig):
                        default_auto_field = "django.db.models.BigAutoField"
                        name = "{app_name}"
                    """
                ),
                f"{app_name}/models.py": model_body,
                f"{app_name}/views.py": textwrap.dedent(
                    f"""\
                    from django.http import JsonResponse

                    def overview(request):
                        return JsonResponse({{"app": "{app_name}", "status": "ok"}})
                    """
                ),
                f"{app_name}/urls.py": textwrap.dedent(
                    f"""\
                    from django.urls import path

                    from . import views

                    app_name = "{app_name}"

                    urlpatterns = [
                        path("", views.overview, name="overview"),
                    ]
                    """
                ),
                f"{app_name}/migrations/__init__.py": "",
            }
        )
    return SampleProject(
        slug=slug,
        title="Generated schema stress-test",
        description="Auto-generated multi-app project with chained FKs.",
        project_name=project_name,
        files=files,
    )


SAMPLE_PROJECTS: list[SampleProject] = [
    _polls_bundle(),
    _djdesk_bundle(),
    _generated_bundle(),
]


def ensure_sample_projects(
    root: Path | None = None, *, force: bool = False
) -> list[tuple[str, Path, bool]]:
    """
    Write the curated sample projects to ``root`` (defaults to ``sample_root``).

    Returns a list of tuples ``(slug, path, created)`` where ``created`` is True
    when files were freshly written (False indicates the directory already
    existed and ``force`` was not set).
    """
    target_root = Path(root) if root else sample_root()
    target_root.mkdir(parents=True, exist_ok=True)
    results: list[tuple[str, Path, bool]] = []
    for project in SAMPLE_PROJECTS:
        project_dir = target_root / project.slug
        if project_dir.exists() and not force:
            results.append((project.slug, project_dir, False))
            continue
        if project_dir.exists():
            shutil.rmtree(project_dir)
        project_dir.mkdir(parents=True, exist_ok=True)
        for relative_path, content in project.files.items():
            full_path = project_dir / relative_path
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content.rstrip() + "\n", encoding="utf-8")
        results.append((project.slug, project_dir, True))
    return results
