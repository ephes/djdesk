from __future__ import annotations

from django.db import migrations, models


def apply_pane_targets(apps, schema_editor) -> None:
    DocLink = apps.get_model("inspector", "DocLink")
    lookup = {
        "guide-boot-django": "hello",
        "guide-render-django": "schema",
        "guide-desktop-affordances": "native",
        "guide-safe-automation": "tasks",
        "guide-data-lab": "data-lab",
        "guide-package": "packaging",
    }
    for slug, pane_target in lookup.items():
        DocLink.objects.using(schema_editor.connection.alias).filter(slug=slug).update(
            pane_target=pane_target
        )


def noop(apps, schema_editor) -> None:
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("inspector", "0002_seed_content"),
    ]

    operations = [
        migrations.AddField(
            model_name="doclink",
            name="pane_target",
            field=models.CharField(blank=True, default="", max_length=40),
            preserve_default=False,
        ),
        migrations.RunPython(apply_pane_targets, noop),
    ]
