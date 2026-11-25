from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from unittest import mock

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError, OperationalError, connections, transaction
from django.db.models import QuerySet
from django.db.models.deletion import ProtectedError
from django.test import TransactionTestCase
from django.urls import reverse

from djdesk.inspector.command_runner import CommandExecutionError, CommandResult
from djdesk.inspector.forms import TaskRunForm
from djdesk.inspector.models import TaskPreset, TaskRunRetired, Workspace, WorkspaceTaskRun
from djdesk.inspector.tasks import execute_workspace_task


class TaskLifecycleTests(TransactionTestCase):
    def setUp(self):
        self.workspace = Workspace.objects.create(name="Synthetic", project_path="/synthetic")
        self.preset = TaskPreset.objects.create(
            key="synthetic", label="Check", description="Test", command="python manage.py check"
        )

    def form(self):
        form = TaskRunForm(
            data={"workspace": self.workspace.slug, "preset": self.preset.key, "confirm_safe": "on"}
        )
        self.assertTrue(form.is_valid(), form.errors)
        return form

    def test_rollback_never_dispatches(self):
        with mock.patch("djdesk.inspector.forms.execute_workspace_task") as task:
            task.enqueue.return_value.id = "queued-result"
            with self.assertRaisesRegex(RuntimeError, "rollback"):
                with transaction.atomic():
                    self.form().save()
                    raise RuntimeError("rollback")
            task.enqueue.assert_not_called()
        self.assertFalse(WorkspaceTaskRun.objects.exists())

    def test_commit_dispatches_once_after_other_connection_can_read(self):
        seen = []

        def read_run(pk):
            try:
                return WorkspaceTaskRun.objects.get(pk=pk).status
            finally:
                connections["default"].close()

        def enqueue(pk, database="default"):
            self.assertFalse(connections["default"].in_atomic_block)
            with ThreadPoolExecutor(max_workers=1) as pool:
                seen.append(pool.submit(read_run, pk).result(timeout=5))
            execute_workspace_task.call(pk, database=database)
            return mock.Mock(id="queued-result")

        with (
            mock.patch(
                "djdesk.inspector.forms.execute_workspace_task", new=mock.Mock(enqueue=enqueue)
            ),
            mock.patch(
                "djdesk.inspector.tasks.run_command",
                return_value=CommandResult(
                    exit_code=0, duration=0.1, output_lines=0, safe_prefix=self.preset.command
                ),
            ) as command,
        ):
            with transaction.atomic():
                run = self.form().save()
                self.assertEqual(seen, [])
                self.assertEqual(run.status, WorkspaceTaskRun.Status.REQUESTED)
            self.assertEqual(seen, [WorkspaceTaskRun.Status.REQUESTED])
            command.assert_called_once()
        run.refresh_from_db()
        self.assertEqual(run.result_id, "queued-result")
        self.assertEqual(run.status, "succeeded")

    def test_enqueue_error_cannot_overwrite_claimed_or_finished_run(self):
        for finished in (False, True):

            def enqueue(pk, database="default"):
                run = WorkspaceTaskRun.objects.get(pk=pk)
                run.mark_running()
                run.append_log("Retained output")
                run.flush_log_buffer()
                if finished:
                    run.mark_finished(success=True)
                raise RuntimeError("backend raised after execution")

            with mock.patch(
                "djdesk.inspector.forms.execute_workspace_task", new=mock.Mock(enqueue=enqueue)
            ):
                run = self.form().save()
            expected = "succeeded" if finished else "running"
            self.assertEqual(run.status, expected)
            self.assertIn("Retained output", run.log)

    def test_unclaimed_enqueue_failure_is_settled(self):
        with mock.patch(
            "djdesk.inspector.forms.execute_workspace_task",
            new=mock.Mock(enqueue=mock.Mock(side_effect=RuntimeError)),
        ):
            with self.assertRaises(ValidationError):
                self.form().save()
        run = WorkspaceTaskRun.objects.get()
        self.assertEqual(run.status, "failed")
        self.assertIsNotNone(run.completed_at)
        self.assertIn("Unable to enqueue", run.log)

    def test_explicit_recovery_retains_log_and_never_replays(self):
        run = WorkspaceTaskRun.objects.create(workspace=self.workspace, preset=self.preset)
        run.mark_running()
        run.append_log("Saved output")
        run.flush_log_buffer()
        original_log = run.log
        # The command loads persisted state, as it would after a worker restart.
        call_command(
            "recover_task_runs",
            run_id=[run.pk],
            confirm_workers_stopped=True,
            reason="All owning workers stopped after interruption",
            stdout=StringIO(),
        )
        run.refresh_from_db()
        self.assertEqual(run.status, "failed")
        self.assertEqual(run.log, original_log)
        self.assertTrue(run.metadata["command"]["interrupted"])
        self.assertIsNotNone(run.completed_at)
        with mock.patch("djdesk.inspector.tasks.run_command") as command:
            self.assertTrue(execute_workspace_task.call(run.pk)["interrupted"])
            command.assert_not_called()
        with self.assertRaises(CommandError):
            call_command(
                "recover_task_runs",
                run_id=[run.pk],
                confirm_workers_stopped=True,
                reason="Stopped",
                stdout=StringIO(),
            )

    def test_recovery_retires_paused_worker_publications(self):
        for late_action in ("success", "log", "error"):
            with self.subTest(late_action=late_action):
                run = WorkspaceTaskRun.objects.create(workspace=self.workspace, preset=self.preset)
                recovered = {}

                def command(**kwargs):
                    call_command(
                        "recover_task_runs",
                        run_id=[run.pk],
                        confirm_workers_stopped=True,
                        reason="Synthetic stopped worker",
                        stdout=StringIO(),
                    )
                    recovered.update(WorkspaceTaskRun.objects.filter(pk=run.pk).values().get())
                    if late_action == "error":
                        raise RuntimeError("Late command error")
                    if late_action == "log":
                        for _ in range(WorkspaceTaskRun.LOG_BATCH_SIZE):
                            kwargs["log_callback"]("Late output")
                    return CommandResult(0, 0.1, 0, self.preset.command)

                with mock.patch("djdesk.inspector.tasks.run_command", side_effect=command):
                    if late_action == "error":
                        with self.assertRaisesRegex(RuntimeError, "Late command error"):
                            execute_workspace_task.call(run.pk)
                    else:
                        payload = execute_workspace_task.call(run.pk)
                        self.assertTrue(payload["retired"])
                        self.assertEqual(payload["status"], "failed")
                self.assertEqual(
                    WorkspaceTaskRun.objects.filter(pk=run.pk).values().get(), recovered
                )

    def test_retired_completion_leaves_the_worker_instance_unchanged(self):
        run = WorkspaceTaskRun.objects.create(
            workspace=self.workspace, preset=self.preset, progress=40, metadata={"notes": "keep"}
        )
        self.assertTrue(run.mark_running())
        call_command(
            "recover_task_runs",
            run_id=[run.pk],
            confirm_workers_stopped=True,
            reason="Synthetic stopped worker",
            stdout=StringIO(),
        )
        persisted = WorkspaceTaskRun.objects.filter(pk=run.pk).values()
        recovered = persisted.get()

        with self.assertRaises(TaskRunRetired):
            run.mark_finished(success=True, command={"exit_code": 0})

        self.assertEqual(run.status, WorkspaceTaskRun.Status.RUNNING)
        self.assertEqual(run.progress, 40)
        self.assertIsNone(run.completed_at)
        self.assertEqual(run.metadata, {"notes": "keep"})
        self.assertEqual(persisted.get(), recovered)

    def test_terminal_update_failure_leaves_status_and_receipt_unsettled(self):
        for outcome in (CommandResult(0, 0.1, 0, "check"), CommandExecutionError("Stopped")):
            with self.subTest(outcome=outcome):
                run = WorkspaceTaskRun.objects.create(
                    workspace=self.workspace, preset=self.preset, metadata={"notes": "keep"}
                )
                runner = (
                    {"side_effect": outcome}
                    if isinstance(outcome, Exception)
                    else {"return_value": outcome}
                )

                with (
                    mock.patch("djdesk.inspector.tasks.run_command", **runner) as command,
                    mock.patch.object(
                        WorkspaceTaskRun, "mark_finished", side_effect=OSError("write failed")
                    ),
                    mock.patch("djdesk.inspector.tasks._record_unexpected_failure"),
                    self.assertRaisesRegex(OSError, "write failed"),
                ):
                    execute_workspace_task.call(run.pk)
                command.assert_called_once()
                run.refresh_from_db()
                self.assertEqual(run.status, WorkspaceTaskRun.Status.RUNNING)
                self.assertIsNone(run.completed_at)
                self.assertEqual(run.metadata, {"notes": "keep"})

    def test_terminal_update_publishes_status_and_command_receipt(self):
        for outcome in (CommandResult(0, 0.1, 0, "check"), CommandExecutionError("Stopped")):
            with self.subTest(outcome=outcome):
                run = WorkspaceTaskRun.objects.create(
                    workspace=self.workspace, preset=self.preset, metadata={"notes": "keep"}
                )
                failed = isinstance(outcome, Exception)
                runner = {"side_effect": outcome} if failed else {"return_value": outcome}
                with mock.patch("djdesk.inspector.tasks.run_command", **runner):
                    payload = execute_workspace_task.call(run.pk)
                run.refresh_from_db()
                self.assertEqual(run.status, "failed" if failed else "succeeded")
                self.assertEqual(run.progress, 100)
                self.assertIsNotNone(run.completed_at)
                self.assertEqual(run.metadata, {"notes": "keep", "command": payload})
                if failed:
                    self.assertEqual(payload["error"], "Stopped")
                else:
                    self.assertEqual(payload["exit_code"], 0)

    def test_unprintable_runner_error_settles_run_and_preserves_original_error(self):
        class BrokenDiagnosticError(Exception):
            def __str__(self):
                raise RuntimeError("formatting failed")

        run = WorkspaceTaskRun.objects.create(workspace=self.workspace, preset=self.preset)
        error = BrokenDiagnosticError()
        with (
            mock.patch("djdesk.inspector.tasks.run_command", side_effect=error),
            self.assertRaises(BrokenDiagnosticError) as raised,
        ):
            execute_workspace_task.call(run.pk)
        self.assertIs(raised.exception, error)
        run.refresh_from_db()
        self.assertEqual(run.status, WorkspaceTaskRun.Status.FAILED)
        self.assertEqual(run.progress, 100)
        self.assertIsNotNone(run.completed_at)
        diagnostic = "Command execution failed unexpectedly: BrokenDiagnosticError"
        self.assertEqual(run.metadata["command"]["error"], diagnostic)
        self.assertIn(diagnostic, run.log)

    def test_recovery_before_terminal_publication_wins(self):
        run = WorkspaceTaskRun.objects.create(workspace=self.workspace, preset=self.preset)
        finish = WorkspaceTaskRun.mark_finished
        recovered = {}

        def recover_then_finish(instance, **kwargs):
            call_command(
                "recover_task_runs",
                run_id=[run.pk],
                confirm_workers_stopped=True,
                reason="Synthetic stopped worker",
                stdout=StringIO(),
            )
            recovered.update(WorkspaceTaskRun.objects.filter(pk=run.pk).values().get())
            return finish(instance, **kwargs)

        with (
            mock.patch(
                "djdesk.inspector.tasks.run_command",
                return_value=CommandResult(0, 0.1, 0, self.preset.command),
            ),
            mock.patch.object(
                WorkspaceTaskRun, "mark_finished", autospec=True, side_effect=recover_then_finish
            ),
        ):
            self.assertTrue(execute_workspace_task.call(run.pk)["retired"])
        self.assertEqual(WorkspaceTaskRun.objects.filter(pk=run.pk).values().get(), recovered)

    def test_recovery_settles_undelivered_request_and_unblocks_parent_deletion(self):
        run = WorkspaceTaskRun.objects.create(workspace=self.workspace, preset=self.preset)
        with self.assertRaises(ProtectedError):
            self.workspace.delete()
        call_command(
            "recover_task_runs",
            run_id=[run.pk],
            confirm_workers_stopped=True,
            reason="Enqueue lost after commit; workers stopped",
            stdout=StringIO(),
        )
        run.refresh_from_db()
        self.assertEqual(run.status, "failed")
        self.assertIsNone(run.started_at)
        self.assertIsNotNone(run.completed_at)
        self.assertTrue(run.metadata["command"]["never_started"])
        self.assertFalse(run.metadata["command"]["interrupted"])
        # A late delivery of the lost message can no longer claim or run the command.
        with mock.patch("djdesk.inspector.tasks.run_command") as command:
            self.assertTrue(execute_workspace_task.call(run.pk)["never_started"])
            command.assert_not_called()
        run.refresh_from_db()
        self.assertEqual(run.status, "failed")
        self.workspace.delete()
        self.assertFalse(WorkspaceTaskRun.objects.filter(pk=run.pk).exists())

    def test_recovery_of_request_claimed_meanwhile_changes_nothing(self):
        run = WorkspaceTaskRun.objects.create(workspace=self.workspace, preset=self.preset)
        stale = WorkspaceTaskRun.objects.get(pk=run.pk)
        run.mark_running()
        real = WorkspaceTaskRun.objects.using("default")

        def snapshot_then_real_write(**lookups):
            # The read returns the stale REQUESTED snapshot; the conditional write runs
            # the command's own lookups against the row a worker has since claimed.
            return [stale] if "pk__in" in lookups else real.filter(**lookups)

        with mock.patch(
            "djdesk.inspector.management.commands.recover_task_runs.WorkspaceTaskRun.objects"
        ) as manager:
            manager.using.return_value.filter.side_effect = snapshot_then_real_write
            with self.assertRaisesRegex(CommandError, "changed during recovery"):
                call_command(
                    "recover_task_runs",
                    run_id=[run.pk],
                    confirm_workers_stopped=True,
                    reason="Stale snapshot",
                    stdout=StringIO(),
                )
        run.refresh_from_db()
        self.assertEqual(run.status, "running")

    def test_recovery_requires_confirmation_and_only_selected_running_rows(self):
        selected = WorkspaceTaskRun.objects.create(workspace=self.workspace, preset=self.preset)
        other = WorkspaceTaskRun.objects.create(workspace=self.workspace, preset=self.preset)
        selected.mark_running()
        other.mark_running()
        for options in (
            {"run_id": [selected.pk], "reason": "Stopped"},
            {"run_id": [selected.pk], "confirm_workers_stopped": True},
            {"run_id": [selected.pk, 999999], "confirm_workers_stopped": True, "reason": "Stopped"},
        ):
            with self.assertRaises(CommandError):
                call_command("recover_task_runs", stdout=StringIO(), **options)
        selected.refresh_from_db()
        self.assertEqual(selected.status, "running")
        call_command(
            "recover_task_runs",
            run_id=[selected.pk],
            confirm_workers_stopped=True,
            reason="Stopped",
            stdout=StringIO(),
        )
        other.refresh_from_db()
        self.assertEqual(other.status, "running")

    def test_create_returns_receipt_when_post_dispatch_run_refresh_fails(self):
        def enqueue(pk, *, database):
            execute_workspace_task.call(pk, database=database)
            return mock.Mock(id="accepted-result")

        with (
            mock.patch("djdesk.inspector.forms.execute_workspace_task") as task,
            mock.patch(
                "djdesk.inspector.tasks.run_command",
                return_value=CommandResult(0, 0.1, 0, "check"),
            ) as command,
            mock.patch.object(
                WorkspaceTaskRun, "refresh_from_db", side_effect=OperationalError("read failed")
            ),
            self.assertLogs("djdesk.inspector.forms", level="WARNING"),
        ):
            task.enqueue.side_effect = enqueue
            response = self.client.post(
                reverse("inspector:task-run-create"),
                {"workspace": self.workspace.slug, "preset": self.preset.key, "confirm_safe": "on"},
            )

        self.assertEqual(response.status_code, 201)
        run = WorkspaceTaskRun.objects.get(workspace=self.workspace)
        self.assertEqual(run.result_id, "accepted-result")
        self.assertEqual(
            response.json()["task_run"],
            {"id": run.pk, "status": "requested", "progress": 0, "log": ""},
        )
        task.enqueue.assert_called_once_with(run.pk, database="default")
        command.assert_called_once()
        detail = self.client.get(reverse("inspector:task-run-detail", args=[run.pk]))
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["status"], "succeeded")

    def test_failed_run_refresh_keeps_outer_transaction_usable(self):
        savepoints = []

        def failing_read(**kwargs):
            savepoints.append(list(connections["default"].savepoint_ids))
            with connections["default"].cursor() as cursor:
                cursor.execute(f"SELECT missing_column FROM {WorkspaceTaskRun._meta.db_table}")

        with (
            mock.patch("djdesk.inspector.forms.execute_workspace_task") as task,
            mock.patch.object(WorkspaceTaskRun, "refresh_from_db", side_effect=failing_read),
            self.assertLogs("djdesk.inspector.forms", level="WARNING"),
        ):
            task.enqueue.return_value.id = "queued-result"
            with transaction.atomic():
                run = self.form().save()
                self.assertEqual(run.status, WorkspaceTaskRun.Status.REQUESTED)
                # SQLite stays usable without a savepoint, so assert that the read had one.
                self.assertEqual(len(savepoints), 1)
                self.assertEqual(len(savepoints[0]), 1)
                self.assertIsNotNone(savepoints[0][0])
                runs = WorkspaceTaskRun.objects.filter(workspace=self.workspace)
                self.assertEqual(runs.count(), 1)
                task.enqueue.assert_not_called()
            task.enqueue.assert_called_once_with(run.pk, database="default")
        self.assertEqual(WorkspaceTaskRun.objects.get(pk=run.pk).result_id, "queued-result")

    def test_create_returns_receipt_when_optional_status_refresh_fails(self):
        dashboard = self.client.get(
            reverse("inspector:dashboard"), {"workspace": self.workspace.slug}
        )
        self.assertContains(dashboard, 'data-task-receipt role="status"')
        for failure in (
            {"side_effect": RuntimeError("snapshot failed")},
            {
                "return_value": {
                    "unserializable": object(),
                }
            },
        ):
            with (
                self.subTest(failure=failure),
                mock.patch("djdesk.inspector.forms.execute_workspace_task") as task,
                mock.patch("djdesk.inspector.views.workspace_status_payload", **failure),
                self.assertLogs("djdesk.inspector.views", level="ERROR"),
            ):
                task.enqueue.return_value.id = "queued-result"
                response = self.client.post(
                    reverse("inspector:task-run-create"),
                    {
                        "workspace": self.workspace.slug,
                        "preset": self.preset.key,
                        "confirm_safe": "on",
                    },
                )
                self.assertEqual(response.status_code, 201)
                receipt = response.json()["task_run"]
                run = WorkspaceTaskRun.objects.get(pk=receipt["id"])
                task.enqueue.assert_called_once_with(run.pk, database="default")
                self.assertEqual(run.result_id, "queued-result")
                self.assertEqual(receipt["status"], WorkspaceTaskRun.Status.REQUESTED)
                self.assertEqual(set(response.json()), {"task_run"})
                detail = self.client.get(reverse("inspector:task-run-detail", args=[receipt["id"]]))
                self.assertEqual(detail.status_code, 200)
                self.assertEqual(detail.json()["id"], run.pk)
                self.assertEqual(detail.json()["log"], run.log)

    def test_create_returns_receipt_when_status_failure_logging_raises(self):
        with (
            mock.patch("djdesk.inspector.forms.execute_workspace_task") as task,
            mock.patch(
                "djdesk.inspector.views.workspace_status_payload",
                side_effect=RuntimeError("snapshot failed"),
            ),
            mock.patch(
                "djdesk.inspector.views.logger.exception", side_effect=OSError("log sink failed")
            ) as report_failure,
        ):
            task.enqueue.return_value.id = "queued-result"
            response = self.client.post(
                reverse("inspector:task-run-create"),
                {"workspace": self.workspace.slug, "preset": self.preset.key, "confirm_safe": "on"},
            )

        self.assertEqual(response.status_code, 201)
        run = WorkspaceTaskRun.objects.get(workspace=self.workspace)
        self.assertEqual(
            response.json(),
            {"task_run": {"id": run.pk, "status": "requested", "progress": 0, "log": run.log}},
        )
        task.enqueue.assert_called_once_with(run.pk, database="default")
        report_failure.assert_called_once_with("Task run %s created; status refresh failed", run.pk)

    def test_create_keeps_accepted_run_when_result_id_write_fails(self):
        update = QuerySet.update

        def fail_result_id(queryset, **fields):
            if "result_id" in fields:
                raise DatabaseError("synthetic backend detail " * 200)
            return update(queryset, **fields)

        for immediate in (False, True):

            def enqueue(pk, *, database):
                if immediate:
                    execute_workspace_task.call(pk, database=database)
                return mock.Mock(id="accepted-result")

            with (
                self.subTest(immediate=immediate),
                mock.patch("djdesk.inspector.forms.execute_workspace_task") as task,
                mock.patch(
                    "djdesk.inspector.tasks.run_command",
                    return_value=CommandResult(0, 0.1, 0, "check"),
                ),
                mock.patch.object(QuerySet, "update", autospec=True, side_effect=fail_result_id),
                self.assertLogs("djdesk.inspector.forms", level="WARNING") as logs,
            ):
                task.enqueue.side_effect = enqueue
                response = self.client.post(
                    reverse("inspector:task-run-create"),
                    {
                        "workspace": self.workspace.slug,
                        "preset": self.preset.key,
                        "confirm_safe": "on",
                    },
                )
                self.assertEqual(response.status_code, 201)
                receipt = response.json()["task_run"]
                run = WorkspaceTaskRun.objects.get(pk=receipt["id"])
                task.enqueue.assert_called_once_with(run.pk, database="default")
                expected = "succeeded" if immediate else "requested"
                self.assertEqual((receipt["status"], run.status), (expected, expected))
                self.assertEqual(run.result_id, "")
                self.assertIn(f"Task run {run.pk}", logs.output[0])
                self.assertNotIn("synthetic backend detail", logs.output[0])
                if immediate:
                    self.assertEqual(run.metadata["command"]["exit_code"], 0)

    def test_view_reports_immediate_enqueue_failure(self):
        with (
            mock.patch(
                "djdesk.inspector.forms.execute_workspace_task",
                new=mock.Mock(enqueue=mock.Mock(side_effect=RuntimeError)),
            ),
            mock.patch.object(
                WorkspaceTaskRun, "refresh_from_db", side_effect=OperationalError("read failed")
            ),
        ):
            response = self.client.post(
                reverse("inspector:task-run-create"),
                {"workspace": self.workspace.slug, "preset": self.preset.key, "confirm_safe": "on"},
            )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Unable to dispatch", response.content.decode())
        self.assertEqual(WorkspaceTaskRun.objects.get().status, "failed")
