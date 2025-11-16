from django import template

register = template.Library()

SCAN_ICON_MAP = {
    "schema": "database",
    "migrations": "git-commit",
    "logs": "activity",
    "fixtures": "archive",
}


@register.filter
def scan_icon(kind: str | None) -> str:
    """Return the Lucide icon for a scan kind."""
    if not kind:
        return SCAN_ICON_MAP["schema"]
    return SCAN_ICON_MAP.get(kind, SCAN_ICON_MAP["schema"])
