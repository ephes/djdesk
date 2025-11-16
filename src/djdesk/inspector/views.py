from __future__ import annotations

from typing import Any

from django.conf import settings
from django.contrib import messages
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.decorators.http import require_GET
from django.views.generic import FormView, TemplateView

from .forms import TaskRunForm, WorkspaceWizardForm
from .models import DocLink, TaskPreset, Workspace, WorkspaceTaskRun
from .services import workspace_status_payload


class DashboardView(TemplateView):
    """Convexity-inspired inspector canvas."""

    template_name = "inspector/dashboard.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        workspaces = list(Workspace.objects.prefetch_related("scans", "task_runs"))
        workspace = self._resolve_workspace(workspaces)
        status_meta: dict[str, Any] = {}
        status_url = ""
        if workspace and workspace.slug:
            status_meta = workspace.metadata.get("status", {})
            status_url = reverse(
                "inspector:workspace-status", kwargs={"slug": workspace.slug}
            )

        context.update(
            {
                "workspace": workspace,
                "workspaces": workspaces,
                "inspector_flags": settings.INSPECTOR_FLAGS,
                "docs_base_url": settings.INSPECTOR_DOCS_BASE_URL,
                "doc_links": DocLink.objects.all(),
                "task_presets": TaskPreset.objects.all(),
                "status_meta": status_meta,
                "workspace_status_url": status_url,
                "task_form": TaskRunForm(
                    initial={"workspace": workspace.slug if workspace else None}
                ),
            }
        )
        return context

    def _resolve_workspace(self, workspaces: Any) -> Workspace | None:
        slug = self.request.GET.get("workspace")
        if slug:
            return next((ws for ws in workspaces if ws.slug == slug), None)
        return next(iter(workspaces), None)


class WorkspaceWizardView(FormView):
    """Guides contributors through project import."""

    template_name = "inspector/wizard.html"
    form_class = WorkspaceWizardForm
    success_url = reverse_lazy("inspector:dashboard")

    def form_valid(self, form: WorkspaceWizardForm) -> HttpResponse:
        workspace = form.save()
        messages.success(
            self.request,
            f"Workspace '{workspace.name}' is ready. Schema scans are queued.",
        )
        return redirect(f"{self.success_url}?workspace={workspace.slug}")


class TaskRunCreateView(View):
    """Accepts POSTs from the assistant drawer and queues a django-tasks job."""

    def post(self, request: HttpRequest) -> JsonResponse:
        form = TaskRunForm(request.POST, initial={"requested_by": "inspector-ui"})
        if not form.is_valid():
            return JsonResponse({"errors": form.errors}, status=400)

        run = form.save()
        payload = workspace_status_payload(run.workspace)
        payload["task_run"] = {
            "id": run.pk,
            "status": run.status,
            "progress": run.progress,
            "log": run.log,
        }
        return JsonResponse(payload, status=201)


@require_GET
def workspace_status_api(request: HttpRequest, slug: str) -> JsonResponse:
    workspace = get_object_or_404(Workspace, slug=slug)
    payload = workspace_status_payload(workspace)
    return JsonResponse(payload)


@require_GET
def task_run_detail_api(request: HttpRequest, pk: int) -> JsonResponse:
    run = get_object_or_404(WorkspaceTaskRun.objects.select_related("preset"), pk=pk)
    data = {
        "id": run.pk,
        "preset": run.preset.key,
        "label": run.preset.label,
        "status": run.status,
        "progress": run.progress,
        "log": run.log,
        "metadata": run.metadata,
    }
    return JsonResponse(data)
