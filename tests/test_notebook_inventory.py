import os
import time
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
        self.directory.mkdir()
        data_lab._export_name_cache.clear()
        self.addCleanup(data_lab._export_name_cache.clear)

    def settled(self):
        # Directory timestamps are cached only once clearly older than the racy window.
        future = time.time_ns() + 10 * data_lab.EXPORT_RACY_WINDOW_NS
        return mock.patch.object(data_lab.time, "time_ns", return_value=future)

    def notebook_mtime(self, path, value):
        identity = path.stat()
        fstat = os.fstat

        def changed_mtime(fd):
            info = fstat(fd)
            if os.path.samestat(identity, info):
                return os.stat_result((*info[:8], value, info.st_ctime))
            return info

        return mock.patch.object(data_lab.os, "fstat", side_effect=changed_mtime)

    def test_undisplayable_timestamps_omit_only_the_bad_notebook(self):
        bad = self.directory / "bad-time.ipynb"
        bad.write_text('{"cells": []}')
        good = data_lab.export_notebook(self.workspace, "schema-audit")
        os.utime(good, (1234567890, 1234567890))
        status_url = reverse("inspector:workspace-status", args=[self.workspace.slug])
        for timestamp in (
            -62135596801,  # Before year 1.
            253402300800,  # After year 9999.
            -1e300,
            1e300,
            float("nan"),
            float("-inf"),
            float("inf"),
        ):
            with self.subTest(timestamp=timestamp), self.notebook_mtime(bad, timestamp):
                exports, incomplete = data_lab.workspace_export_inventory(self.workspace)
                self.assertEqual([entry["slug"] for entry in exports], ["schema-audit"])
                self.assertTrue(incomplete)
                response = self.client.get(status_url)
                self.assertEqual(response.status_code, 200)
                payload = response.json()["data_lab"]
                self.assertEqual([row["slug"] for row in payload["notebooks"]], ["schema-audit"])
                self.assertEqual(
                    payload["notebooks"][0]["modified_at"], "2009-02-13T23:31:30+00:00"
                )
                self.assertTrue(payload["notebooks_incomplete"])

    def test_displayable_timestamp_boundaries_remain_exact_and_complete(self):
        path = data_lab.export_notebook(self.workspace, "schema-audit")
        for timestamp, expected in (
            (-62135596800, "0001-01-01T00:00:00+00:00"),
            (0, "1970-01-01T00:00:00+00:00"),
            (253402300799, "9999-12-31T23:59:59+00:00"),
        ):
            with self.subTest(timestamp=timestamp), self.notebook_mtime(path, timestamp):
                exports, incomplete = data_lab.workspace_export_inventory(self.workspace)
                self.assertEqual(exports[0]["modified_at"], timestamp)
                self.assertFalse(incomplete)
                payload = workspace_data_lab_payload(self.workspace)
                self.assertEqual(payload["notebooks"][0]["modified_at"], expected)
                self.assertFalse(payload["notebooks_incomplete"])

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
            self.settled(),
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

    def test_nonregular_notebook_names_do_not_consume_the_result_budget(self):
        for slug in ("z-last", "m-first", "s-middle"):
            (self.directory / f"{slug}.ipynb").write_text('{"cells": []}')
        for index in range(data_lab.EXPORT_RESULT_LIMIT + 1):
            path = self.directory / f"aaa-{index:04}.ipynb"
            if index % 2:
                path.symlink_to("z-last.ipynb")
            else:
                path.mkdir()

        payload = workspace_data_lab_payload(self.workspace)
        self.assertEqual(
            [entry["slug"] for entry in payload["notebooks"]], ["m-first", "s-middle", "z-last"]
        )
        self.assertFalse(payload["notebooks_incomplete"])

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
        with self.settled():
            before = workspace_data_lab_payload(self.workspace)
        os.utime(path, (1234567890, 1234567890))
        with (
            self.settled(),
            mock.patch.object(os, "scandir", side_effect=AssertionError("unnecessary scan")),
        ):
            after = workspace_data_lab_payload(self.workspace)
        self.assertNotEqual(
            before["notebooks"][0]["modified_at"], after["notebooks"][0]["modified_at"]
        )

    def test_recent_generation_is_rescanned_when_timestamps_do_not_move(self):
        # Coarse filesystem clocks can leave mtime/ctime unchanged after a same-tick export.
        frozen = data_lab._directory_generation(self.directory)
        with mock.patch.object(data_lab, "_directory_generation", return_value=frozen):
            self.assertEqual(workspace_data_lab_payload(self.workspace)["notebooks"], [])
            (self.directory / "late.ipynb").write_text('{"cells": []}')
            payload = workspace_data_lab_payload(self.workspace)
        self.assertEqual([entry["slug"] for entry in payload["notebooks"]], ["late"])
        self.assertFalse(payload["notebooks_incomplete"])

    def test_settled_generation_is_cached_without_rescanning(self):
        data_lab.export_notebook(self.workspace, "schema-audit")
        with self.settled():
            first = workspace_data_lab_payload(self.workspace)
            with mock.patch.object(os, "scandir", side_effect=AssertionError("unnecessary scan")):
                second = workspace_data_lab_payload(self.workspace)
        self.assertEqual(first, second)

    def test_generation_change_during_scan_is_retried_instead_of_reported_empty(self):
        data_lab.export_notebook(self.workspace, "schema-audit")
        real_scan = data_lab._scan_export_names
        calls = []

        def racing_scan(directory, generation):
            calls.append(generation)
            if len(calls) == 1:
                raise data_lab._GenerationChanged("synthetic concurrent export")
            return real_scan(directory, generation)

        with mock.patch.object(data_lab, "_scan_export_names", side_effect=racing_scan):
            payload = workspace_data_lab_payload(self.workspace)
        self.assertEqual(len(calls), 2)
        self.assertEqual([entry["slug"] for entry in payload["notebooks"]], ["schema-audit"])
        self.assertFalse(payload["notebooks_incomplete"])

    def test_persistently_changing_directory_reports_unavailable_inventory(self):
        with mock.patch.object(
            data_lab,
            "_scan_export_names",
            side_effect=data_lab._GenerationChanged("synthetic churn"),
        ) as scan:
            payload = workspace_data_lab_payload(self.workspace)
        self.assertEqual(scan.call_count, data_lab.EXPORT_SCAN_ATTEMPTS)
        self.assertEqual(payload["notebooks"], [])
        self.assertTrue(payload["notebooks_incomplete"])
