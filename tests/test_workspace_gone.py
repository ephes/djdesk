from unittest import mock

from django.test import TestCase
from django.urls import reverse

from djdesk.inspector import services, views
from djdesk.inspector.models import Workspace

real_snapshot = services.workspace_snapshot


def deleting_snapshot(workspace, **kwargs):
    # Deterministic deletion after the view's lookup, before the snapshot reload.
    Workspace.objects.filter(pk=workspace.pk).delete()
    return real_snapshot(workspace, **kwargs)


class GoneWorkspaceTests(TestCase):
    def setUp(self):
        self.workspace = Workspace.objects.create(name="Synthetic", project_path="/synthetic/gone")

    def test_status_returns_404_when_deleted_before_snapshot(self):
        url = reverse("inspector:workspace-status", kwargs={"slug": self.workspace.slug})
        with mock.patch.object(services, "workspace_snapshot", side_effect=deleting_snapshot):
            response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_dashboard_drops_selection_deleted_before_snapshot(self):
        url = reverse("inspector:dashboard") + f"?workspace={self.workspace.slug}"
        with mock.patch.object(views, "workspace_snapshot", side_effect=deleting_snapshot):
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["workspace"])
        self.assertEqual(response.context["workspace_status_url"], "")
