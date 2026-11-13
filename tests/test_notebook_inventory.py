import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse

from djdesk.inspector import data_lab
from djdesk.inspector.models import Workspace
from djdesk.inspector.services import workspace_data_lab_payload


class NotebookInventoryTests(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.override = override_settings(INSPECTOR_DATA_LAB_ROOT=Path(self.temp.name))
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.workspace = Workspace.objects.create(name="Synthetic", project_path="/tmp/inventory")
        self.directory = data_lab.workspace_data_lab_dir(self.workspace)

    def test_large_inventory_has_bounded_work_and_direct_view_remains_available(self):
        for index in range(2000):
            (self.directory / f"notes-{index:04}.ipynb").write_text('{"cells": []}')
        visits = []
        scandir = os.scandir

        class TrackedScan:
            def __init__(self, *args, **kwargs):
                self.scan = scandir(*args, **kwargs)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.scan.close()

            def __iter__(self):
                for entry in self.scan:
                    visits.append(entry.name)
                    yield entry

        with (
            mock.patch.object(os, "scandir", TrackedScan),
            mock.patch.object(data_lab, "_open_notebook", wraps=data_lab._open_notebook) as opened,
        ):
            first = workspace_data_lab_payload(self.workspace)
            first_visits = len(visits)
            second = workspace_data_lab_payload(self.workspace)
        self.assertLessEqual(first_visits, 513)
        self.assertEqual(len(visits), first_visits, "unchanged generation must reuse inventory")
        self.assertLessEqual(opened.call_count, 100)
        self.assertLessEqual(len(first["notebooks"]), 50)
        self.assertTrue(first["notebooks_incomplete"])
        self.assertEqual(first, second)
        slugs = [entry["slug"] for entry in first["notebooks"]]
        self.assertEqual(slugs, sorted(slugs))
        omitted = next(
            f"notes-{index:04}" for index in range(2000) if f"notes-{index:04}" not in slugs
        )
        response = self.client.get(
            reverse("inspector:data-lab-notebook", args=[self.workspace.slug, omitted])
        )
        self.assertEqual(response.status_code, 200)

    def test_creation_invalidates_cached_generation_and_small_inventory_is_complete(self):
        self.assertEqual(workspace_data_lab_payload(self.workspace)["notebooks"], [])
        data_lab.export_notebook(self.workspace, "schema-audit")
        payload = workspace_data_lab_payload(self.workspace)
        self.assertEqual([entry["slug"] for entry in payload["notebooks"]], ["schema-audit"])
        self.assertFalse(payload["notebooks_incomplete"])

    def test_nonnotebook_entries_also_consume_the_scan_budget(self):
        for index in range(1000):
            (self.directory / f"ignored-{index}").touch()
        payload = workspace_data_lab_payload(self.workspace)
        self.assertEqual(payload["notebooks"], [])
        self.assertTrue(payload["notebooks_incomplete"])

    def test_export_response_selects_new_notebook_outside_the_inventory_window(self):
        for index in range(55):
            (self.directory / f"aaa-{index:04}.ipynb").write_text('{"cells": []}')
        response = self.client.post(
            reverse("inspector:data-lab-export", args=[self.workspace.slug]),
            {"template": "schema-audit"},
        )
        self.assertEqual(response.status_code, 201)
        payload = response.json()["data_lab"]
        self.assertTrue(payload["notebooks_incomplete"])
        self.assertNotIn("schema-audit", [entry["slug"] for entry in payload["notebooks"]])
        self.assertEqual(payload["selected_notebook"], "schema-audit")
        self.assertEqual(self.client.get(payload["selected_notebook_url"]).status_code, 200)

    def test_file_edits_refresh_metadata_without_reenumerating_the_directory(self):
        path = data_lab.export_notebook(self.workspace, "schema-audit")
        before = workspace_data_lab_payload(self.workspace)
        os.utime(path, (1234567890, 1234567890))
        with mock.patch.object(os, "scandir", side_effect=AssertionError("unnecessary scan")):
            after = workspace_data_lab_payload(self.workspace)
        self.assertNotEqual(
            before["notebooks"][0]["modified_at"], after["notebooks"][0]["modified_at"]
        )
