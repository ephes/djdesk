from django.apps import AppConfig


class InspectorConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "djdesk.inspector"
    verbose_name = "DJDesk Project Inspector"

    def ready(self) -> None:  # pragma: no cover - import side effects
        # Import the django-tasks definitions so they are registered.
        from . import tasks  # noqa: F401
