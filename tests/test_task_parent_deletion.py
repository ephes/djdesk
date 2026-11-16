from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest import mock

from django.contrib import admin
from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import connections, transaction
from django.db.models.deletion import Collector, ProtectedError
from django.test import TransactionTestCase
from django.urls import reverse

from djdesk.inspector.command_runner import CommandResult
from djdesk.inspector.models import TaskPreset, Workspace, WorkspaceTaskRun
from djdesk.inspector.tasks import execute_workspace_task


class TaskParentDeletionTests(TransactionTestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("operator"))
        self.workspace = Workspace.objects.create(name="Synthetic", project_path="/synthetic")
        self.preset = TaskPreset.objects.create(
            key="synthetic", label="Check", description="Test", command="python manage.py check"
        )
        self.run = WorkspaceTaskRun.objects.create(workspace=self.workspace, preset=self.preset)
        Workspace.objects.create(name="Idle", project_path="/synthetic/idle")
        TaskPreset.objects.create(
            key="idle", label="Idle", description="Test", command="python manage.py check"
        )

    def test_model_and_queryset_delete_refuse_active_runs_without_partial_deletion(self):
        for model, parent in ((Workspace, self.workspace), (TaskPreset, self.preset)):
            for status in ("requested", "running"):
                for bulk in (False, True):
                    with self.subTest(model=model.__name__, status=status, bulk=bulk):
                        WorkspaceTaskRun.objects.filter(pk=self.run.pk).update(status=status)
                        before = list(model.objects.values())
                        with self.assertRaises(ProtectedError), transaction.atomic():
                            if bulk:
                                model.objects.all().delete()
                            else:
                                model.objects.get(pk=parent.pk).delete()
                        self.assertEqual(list(model.objects.values()), before)
                        self.assertTrue(WorkspaceTaskRun.objects.filter(pk=self.run.pk).exists())

    def test_real_worker_survives_single_and_bulk_admin_delete_attempts(self):
        entered, release = Event(), Event()

        def command(**kwargs):
            entered.set()
            if not release.wait(10):
                raise RuntimeError("Synthetic barrier timed out")
            return CommandResult(0, 0.1, 0, "check")

        def worker():
            try:
                return execute_workspace_task.call(self.run.pk)
            finally:
                connections["default"].close()

        with mock.patch("djdesk.inspector.tasks.run_command", side_effect=command):
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(worker)
                try:
                    self.assertTrue(entered.wait(10))
                    for model, parent in ((Workspace, self.workspace), (TaskPreset, self.preset)):
                        name = model._meta.model_name
                        before = list(model.objects.values())
                        single_url = reverse(f"admin:inspector_{name}_delete", args=[parent.pk])
                        for response in (
                            self.client.get(single_url),
                            self.client.post(single_url, {"post": "yes"}),
                            self.client.post(
                                reverse(f"admin:inspector_{name}_changelist"),
                                {"action": "delete_selected", "_selected_action": parent.pk},
                            ),
                            self.client.post(
                                reverse(f"admin:inspector_{name}_changelist"),
                                {
                                    "action": "delete_selected",
                                    "_selected_action": list(
                                        model.objects.values_list("pk", flat=True)
                                    ),
                                    "post": "yes",
                                },
                            ),
                        ):
                            self.assertEqual(response.status_code, 200)
                            self.assertTrue(response.context["protected"])
                        self.assertEqual(list(model.objects.values()), before)
                        self.assertEqual(
                            WorkspaceTaskRun.objects.get(pk=self.run.pk).status, "running"
                        )
                finally:
                    release.set()
                future.result(timeout=10)
        self.run.refresh_from_db()
        self.assertEqual(self.run.status, "succeeded")

    def test_terminal_history_cascades_with_single_and_bulk_parent_deletion(self):
        for model in (Workspace, TaskPreset):
            for status in ("succeeded", "failed", "cancelled"):
                for bulk in (False, True):
                    with self.subTest(model=model.__name__, status=status, bulk=bulk):
                        workspace = Workspace.objects.create(
                            name="Terminal",
                            project_path=f"/terminal/{model.__name__}/{status}/{bulk}",
                        )
                        preset = TaskPreset.objects.create(
                            key=f"terminal-{model.__name__.lower()}-{status}-{bulk}",
                            label="Terminal",
                            description="Test",
                            command="python manage.py check",
                        )
                        run = WorkspaceTaskRun.objects.create(
                            workspace=workspace, preset=preset, status=status
                        )
                        parent = workspace if model is Workspace else preset
                        name = model._meta.model_name
                        response = self.client.post(
                            reverse(f"admin:inspector_{name}_changelist")
                            if bulk
                            else reverse(f"admin:inspector_{name}_delete", args=[parent.pk]),
                            {
                                "action": "delete_selected",
                                "_selected_action": parent.pk,
                                "post": "yes",
                            },
                        )
                        self.assertEqual(response.status_code, 302)
                        self.assertFalse(model.objects.filter(pk=parent.pk).exists())
                        self.assertFalse(WorkspaceTaskRun.objects.filter(pk=run.pk).exists())

    def test_new_request_after_confirmation_prevents_entire_selected_set_deletion(self):
        self.run.status = "succeeded"
        self.run.save(update_fields=["status"])
        for model, parent in ((Workspace, self.workspace), (TaskPreset, self.preset)):
            name = model._meta.model_name
            url = reverse(f"admin:inspector_{name}_delete", args=[parent.pk])
            self.assertFalse(self.client.get(url).context["protected"])
            new_run = WorkspaceTaskRun.objects.create(workspace=self.workspace, preset=self.preset)
            response = self.client.post(url, {"post": "yes"})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context["protected"])
            self.assertTrue(model.objects.filter(pk=parent.pk).exists())
            new_run.delete()

    def test_final_guard_catches_run_inserted_after_cascade_collection(self):
        self.run.status = "succeeded"
        self.run.save(update_fields=["status"])
        for model, parent in ((Workspace, self.workspace), (TaskPreset, self.preset)):
            collector = Collector(using="default")
            collector.collect([parent])
            new_run = WorkspaceTaskRun.objects.create(workspace=self.workspace, preset=self.preset)
            with self.assertRaises(ProtectedError):
                collector.delete()
            self.assertTrue(model.objects.filter(pk=parent.pk).exists())
            self.assertTrue(WorkspaceTaskRun.objects.filter(pk=self.run.pk).exists())
            self.assertTrue(WorkspaceTaskRun.objects.filter(pk=new_run.pk).exists())
            new_run.delete()

    def test_deleting_an_already_removed_parent_remains_a_noop(self):
        self.run.status = "succeeded"
        self.run.save(update_fields=["status"])
        for model, parent in ((Workspace, self.workspace), (TaskPreset, self.preset)):
            stale = model.objects.get(pk=parent.pk)
            parent.delete()
            self.assertEqual(stale.delete()[0], 0)

    def test_admin_race_refusal_rolls_back_deletion_logs_and_whole_selection(self):
        self.run.status = "succeeded"
        self.run.save(update_fields=["status"])
        for model, parent in ((Workspace, self.workspace), (TaskPreset, self.preset)):
            for bulk in (False, True):
                model_admin = admin.site._registry[model]
                hook = "delete_queryset" if bulk else "delete_model"
                delete = getattr(model_admin, hook)

                def request_then_delete(*args):
                    WorkspaceTaskRun.objects.create(workspace=self.workspace, preset=self.preset)
                    delete(*args)

                before = list(model.objects.values())
                name = model._meta.model_name
                with mock.patch.object(model_admin, hook, side_effect=request_then_delete):
                    response = self.client.post(
                        reverse(f"admin:inspector_{name}_changelist")
                        if bulk
                        else reverse(f"admin:inspector_{name}_delete", args=[parent.pk]),
                        {
                            "action": "delete_selected",
                            "_selected_action": list(model.objects.values_list("pk", flat=True))
                            if bulk
                            else parent.pk,
                            "post": "yes",
                        },
                        follow=True,
                    )
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "Deletion blocked")
                self.assertNotContains(response, "Successfully deleted")
                self.assertEqual(list(model.objects.values()), before)
                self.assertFalse(LogEntry.objects.exists())
                self.assertTrue(WorkspaceTaskRun.objects.filter(pk=self.run.pk).exists())

    def test_terminal_history_cascade_requires_run_delete_permission(self):
        self.run.status = "succeeded"
        self.run.save(update_fields=["status"])
        staff = get_user_model().objects.create_user("staff", is_staff=True)
        staff.user_permissions.set(
            Permission.objects.filter(
                content_type__app_label="inspector",
                codename__in=("view_workspace", "change_workspace", "delete_workspace"),
            )
        )
        self.client.force_login(staff)
        url = reverse("admin:inspector_workspace_delete", args=[self.workspace.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["perms_lacking"])
        self.assertEqual(self.client.post(url, {"post": "yes"}).status_code, 403)
        bulk = self.client.post(
            reverse("admin:inspector_workspace_changelist"),
            {"action": "delete_selected", "_selected_action": self.workspace.pk, "post": "yes"},
        )
        self.assertEqual(bulk.status_code, 403)
        self.assertTrue(Workspace.objects.filter(pk=self.workspace.pk).exists())
        self.assertTrue(WorkspaceTaskRun.objects.filter(pk=self.run.pk).exists())

        staff.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="inspector", codename="delete_workspacetaskrun"
            )
        )
        staff = get_user_model().objects.get(pk=staff.pk)
        self.client.force_login(staff)
        self.assertFalse(self.client.get(url).context["perms_lacking"])
        self.assertEqual(self.client.post(url, {"post": "yes"}).status_code, 302)
        self.assertFalse(Workspace.objects.filter(pk=self.workspace.pk).exists())
        self.assertFalse(WorkspaceTaskRun.objects.filter(pk=self.run.pk).exists())
