from django.urls import path

from . import views

app_name = "inspector"

urlpatterns = [
    path("", views.DashboardView.as_view(), name="dashboard"),
    path("wizard/", views.WorkspaceWizardView.as_view(), name="wizard"),
    path(
        "api/workspaces/<slug:slug>/status/",
        views.workspace_status_api,
        name="workspace-status",
    ),
    path(
        "api/task-runs/",
        views.TaskRunCreateView.as_view(),
        name="task-run-create",
    ),
    path(
        "api/task-runs/<int:pk>/",
        views.task_run_detail_api,
        name="task-run-detail",
    ),
]
