from unittest import mock

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from djdesk.inspector.command_runner import CommandResult
from djdesk.inspector.models import TaskPreset, Workspace, WorkspaceTaskRun
from djdesk.inspector.tasks import execute_workspace_task


class TaskRunAdminTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("operator"))
        self.workspace = Workspace.objects.create(name="Synthetic", project_path="/synthetic")
        self.preset = TaskPreset.objects.create(
            key="synthetic", label="Check", description="Test", command="python manage.py check"
        )
        self.run = WorkspaceTaskRun.objects.create(workspace=self.workspace, preset=self.preset)

    def test_stale_and_forged_admin_posts_cannot_replace_worker_outcome(self):
        url = reverse("admin:inspector_workspacetaskrun_change", args=[self.run.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        with mock.patch(
            "djdesk.inspector.tasks.run_command", return_value=CommandResult(0, 0.1, 0, "check")
        ):
            execute_workspace_task.call(self.run.pk)
        before = WorkspaceTaskRun.objects.values().get(pk=self.run.pk)
        other = Workspace.objects.create(name="Other", project_path="/synthetic/other")
        response = self.client.post(
            url,
            {
                "workspace": other.pk,
                "preset": self.preset.pk,
                "status": "requested",
                "progress": 0,
                "requested_by": "forged",
                "metadata": "{}",
                "log": "replaced",
                "result_id": "forged",
                "started_at_0": "",
                "started_at_1": "",
                "completed_at_0": "",
                "completed_at_1": "",
                "_save": "Save",
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(WorkspaceTaskRun.objects.values().get(pk=self.run.pk), before)
        page = self.client.get(url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Succeeded")
        self.assertNotContains(page, 'name="status"')

    def test_admin_cannot_create_or_delete_execution_history(self):
        before = list(WorkspaceTaskRun.objects.values())
        response = self.client.post(
            reverse("admin:inspector_workspacetaskrun_add"),
            {
                "workspace": self.workspace.pk,
                "preset": self.preset.pk,
                "status": "requested",
                "progress": 0,
                "requested_by": "forged",
                "metadata": "{}",
            },
        )
        self.assertEqual(response.status_code, 403)
        response = self.client.post(
            reverse("admin:inspector_workspacetaskrun_delete", args=[self.run.pk]), {"post": "yes"}
        )
        self.assertEqual(response.status_code, 403)
        model_admin = admin.site._registry[WorkspaceTaskRun]
        request = mock.Mock(user=get_user_model().objects.get(username="operator"), GET={})
        self.assertNotIn("delete_selected", model_admin.get_actions(request))
        self.assertEqual(list(WorkspaceTaskRun.objects.values()), before)
