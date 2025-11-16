from django.contrib import admin

from .models import (
    DocLink,
    ScanJob,
    TaskPreset,
    Workspace,
    WorkspaceTaskRun,
)


@admin.register(Workspace)
class WorkspaceAdmin(admin.ModelAdmin):
    list_display = ("name", "project_path", "last_scan_at", "manage_py_detected")
    list_filter = ("manage_py_detected",)
    search_fields = ("name", "project_path")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(ScanJob)
class ScanJobAdmin(admin.ModelAdmin):
    list_display = ("workspace", "kind", "status", "progress", "created_at")
    list_filter = ("kind", "status")
    search_fields = ("workspace__name", "summary")


@admin.register(TaskPreset)
class TaskPresetAdmin(admin.ModelAdmin):
    list_display = ("label", "command", "category", "order")
    list_filter = ("category",)
    search_fields = ("label", "command")


@admin.register(WorkspaceTaskRun)
class WorkspaceTaskRunAdmin(admin.ModelAdmin):
    list_display = ("workspace", "preset", "status", "progress", "created_at")
    list_filter = ("status", "preset")
    search_fields = ("workspace__name", "preset__label")


@admin.register(DocLink)
class DocLinkAdmin(admin.ModelAdmin):
    list_display = ("title", "stage", "url")
    list_filter = ("stage", "category")
    search_fields = ("title", "url")
