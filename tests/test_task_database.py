from copy import deepcopy
from io import StringIO
from unittest import TestCase, mock

from django.core.management import call_command
from django.db import connections, transaction
from django.test import override_settings

from djdesk.inspector.command_runner import CommandResult
from djdesk.inspector.forms import TaskRunForm
from djdesk.inspector.models import TaskPreset, Workspace, WorkspaceTaskRun
from djdesk.inspector.tasks import execute_workspace_task


class TaskRouter:
    def db_for_read(self, model, **hints):
        return "task_primary"

    def db_for_write(self, model, **hints):
        instance = hints.get("instance")
        return instance._state.db if instance else "task_primary"


class TaskDatabaseTests(TestCase):
    aliases = ("task_primary", "task_selected")

    def setUp(self):
        self.enterContext(override_settings(DATABASE_ROUTERS=[TaskRouter()]))
        for alias in self.aliases:
            config = deepcopy(connections["default"].settings_dict)
            config.update(ENGINE="django.db.backends.sqlite3", NAME=":memory:")
            connections.databases[alias] = config
            self.addCleanup(self.close_alias, alias)
            with connections[alias].schema_editor() as editor:
                for model in (Workspace, TaskPreset, WorkspaceTaskRun):
                    editor.create_model(model)
            workspace = Workspace.objects.using(alias).create(
                pk=1, name=alias, slug=alias, project_path=f"/synthetic/{alias}"
            )
            preset = TaskPreset(
                pk=1,
                key=alias,
                label=alias,
                description="Synthetic",
                command="python manage.py check",
            )
            TaskPreset.objects.using(alias).bulk_create([preset])
            WorkspaceTaskRun.objects.using(alias).create(pk=1, workspace=workspace, preset=preset)

    def close_alias(self, alias):
        connections[alias].close()
        del connections[alias]
        del connections.databases[alias]

    def test_claim_stays_on_loaded_database(self):
        primary, selected = self.aliases
        run = WorkspaceTaskRun.objects.using(selected).get(pk=1)
        self.assertTrue(run.mark_running())
        run.refresh_from_db(using=selected)
        self.assertEqual(run.status, "running")
        self.assertEqual(WorkspaceTaskRun.objects.using(primary).get(pk=1).status, "requested")

    def test_execution_and_unexpected_failure_use_selected_database(self):
        primary, selected = self.aliases
        before = list(WorkspaceTaskRun.objects.using(primary).values())
        for error in (None, RuntimeError("synthetic failure")):
            WorkspaceTaskRun.objects.using(selected).filter(pk=1).update(status="requested")
            with mock.patch(
                "djdesk.inspector.tasks.run_command",
                side_effect=error,
                return_value=CommandResult(0, 0.1, 0, "python manage.py check"),
            ) as command:
                if error:
                    with self.assertRaisesRegex(RuntimeError, "synthetic failure"):
                        execute_workspace_task.call(1, database=selected)
                else:
                    execute_workspace_task.call(1, database=selected)
            self.assertEqual(command.call_args.kwargs["workspace_path"], f"/synthetic/{selected}")
            run = WorkspaceTaskRun.objects.using(selected).get(pk=1)
            self.assertEqual(run.status, "failed" if error else "succeeded")
            self.assertTrue(run.log)
            self.assertIn("command", run.metadata)
            self.assertIsNotNone(run.completed_at)
            self.assertEqual(before, list(WorkspaceTaskRun.objects.using(primary).values()))

    def test_form_dispatch_waits_for_selected_database_commit(self):
        selected = self.aliases[1]
        form = TaskRunForm()
        form.cleaned_data = {
            "workspace": Workspace.objects.using(selected).get(pk=1),
            "preset": TaskPreset.objects.using(selected).get(pk=1),
        }
        with mock.patch("djdesk.inspector.forms.execute_workspace_task") as task:
            task.enqueue.return_value.id = "selected-result"
            with transaction.atomic(using=selected):
                run = form.save()
                self.assertEqual(run._state.db, selected)
                task.enqueue.assert_not_called()
            task.enqueue.assert_called_once_with(run.pk, database=selected)
        run.refresh_from_db(using=selected)
        self.assertEqual(run.result_id, "selected-result")

    def test_recovery_uses_selected_database(self):
        primary, selected = self.aliases
        for alias in self.aliases:
            WorkspaceTaskRun.objects.using(alias).get(pk=1).mark_running()
        before = list(WorkspaceTaskRun.objects.using(primary).values())
        call_command(
            "recover_task_runs",
            database=selected,
            run_id=[1],
            confirm_workers_stopped=True,
            reason="Synthetic stopped worker",
            stdout=StringIO(),
        )
        self.assertEqual(WorkspaceTaskRun.objects.using(selected).get(pk=1).status, "failed")
        self.assertEqual(before, list(WorkspaceTaskRun.objects.using(primary).values()))
