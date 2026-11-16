"""Large synthetic inventories retain only a selector page and the active workspace."""

from django.db import connection
from django.db.models.signals import post_init
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from djdesk.inspector.models import Workspace


class WorkspaceSelectorTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Workspace.objects.all().delete()
        Workspace.objects.bulk_create(
            [
                Workspace(
                    name=f"Synthetic {index // 3:04}",
                    slug=f"synthetic-{index}",
                    project_path=f"/synthetic/{index}",
                    metadata={"description": "unselected metadata" * 100},
                )
                for index in range(2003)
            ]
        )

    def test_initial_page_bounds_rows_and_html_without_loading_other_metadata(self):
        loaded = []

        def record(sender, instance, **kwargs):
            loaded.append(instance)

        post_init.connect(record, sender=Workspace)
        try:
            with CaptureQueriesContext(connection) as queries:
                response = self.client.get(reverse("inspector:dashboard"))
        finally:
            post_init.disconnect(record, sender=Workspace)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["workspaces"]), 50)
        self.assertLessEqual(len(loaded), 52)
        selected = response.context["workspace"]
        self.assertTrue(
            all("metadata" in ws.get_deferred_fields() for ws in loaded if ws.pk != selected.pk)
        )
        workspace_reads = [
            query["sql"]
            for query in queries
            if 'FROM "inspector_workspace"' in query["sql"] and "COUNT(" not in query["sql"]
        ]
        self.assertTrue(workspace_reads)
        self.assertTrue(all("LIMIT" in sql for sql in workspace_reads))
        self.assertContains(response, 'class="workspace-name"', count=50)
        self.assertNotContains(response, "unselected metadata")
        self.assertNotContains(response, "/synthetic/2002")

    def test_continuation_has_no_duplicates_even_for_equal_names(self):
        expected = list(Workspace.objects.order_by("name", "pk").values_list("pk", flat=True))
        seen = []
        url = reverse("inspector:dashboard")
        while url:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            rows = response.context["workspaces"]
            self.assertLessEqual(len(rows), 50)
            seen.extend(ws.pk for ws in rows)
            url = response.context["workspace_next_url"]
        self.assertEqual(seen, expected)

    def test_direct_late_workspace_and_search_preserve_selection(self):
        slug = "synthetic-2002"
        response = self.client.get(reverse("inspector:dashboard"), {"workspace": slug})
        self.assertEqual(response.context["workspace"].slug, slug)
        self.assertNotIn(slug, [ws.slug for ws in response.context["workspaces"]])
        response = self.client.get(
            reverse("inspector:dashboard"),
            {"workspace": slug, "workspace_search": "/synthetic/19"},
        )
        self.assertEqual(response.context["workspace"].slug, slug)
        self.assertTrue(response.context["workspaces"])
        self.assertTrue(
            all("/synthetic/19" in ws.project_path for ws in response.context["workspaces"])
        )
        self.assertIn("workspace=synthetic-2002", response.context["workspace_next_url"])
        self.assertIn("workspace_search=%2Fsynthetic%2F19", response.context["workspace_next_url"])
        self.assertContains(response, 'name="workspace" value="synthetic-2002"')

    def test_empty_search_and_invalid_page_are_bounded(self):
        for page in ("not-a-page", "-1", "999999999999999999999999999"):
            response = self.client.get(reverse("inspector:dashboard"), {"workspace_page": page})
            self.assertEqual(response.status_code, 200)
            self.assertLessEqual(len(response.context["workspaces"]), 50)
        response = self.client.get(reverse("inspector:dashboard"), {"workspace_search": "missing"})
        self.assertEqual(response.context["workspaces"], [])
        self.assertContains(response, "No matching workspaces.")

    def test_empty_inventory(self):
        Workspace.objects.all().delete()
        response = self.client.get(reverse("inspector:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["workspace"])
        self.assertEqual(response.context["workspaces"], [])
        self.assertEqual(response.context["workspace_next_url"], "")

    def test_search_matches_names_case_insensitively(self):
        response = self.client.get(
            reverse("inspector:dashboard"), {"workspace_search": "sYnThEtIc 0667"}
        )
        self.assertEqual(
            [ws.slug for ws in response.context["workspaces"]],
            ["synthetic-2001", "synthetic-2002"],
        )
