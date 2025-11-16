from __future__ import annotations

from django.db import migrations, models

from djdesk.inspector import content


def apply_pane_targets(apps, schema_editor) -> None:
    DocLink = apps.get_model("inspector", "DocLink")
    lookup = {entry["slug"]: entry.get("pane_target", "") for entry in content.DOC_LINKS}
    for slug, pane_target in lookup.items():
        DocLink.objects.filter(slug=slug).update(pane_target=pane_target or "")


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
