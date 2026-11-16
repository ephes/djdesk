from django.contrib import admin, messages
from django.db import router, transaction
from django.db.models.deletion import ProtectedError
from django.http import HttpResponseRedirect
from django.urls import reverse

from .models import (
    DocLink,
    ScanJob,
    TaskPreset,
    Workspace,
    WorkspaceTaskRun,
)


class TaskParentAdmin(admin.ModelAdmin):
    def get_queryset(self, request):
        return super().get_queryset(request).using(router.db_for_write(self.model))

    def get_deleted_objects(self, objs, request):
        deleted, counts, perms, protected = super().get_deleted_objects(objs, request)
        # The run admin is read-only for everyone, so Django always lists runs as needing
        # permission. Terminal history may cascade only for users holding the model-level
        # run delete permission.
        run_opts = WorkspaceTaskRun._meta
        if request.user.has_perm(f"{run_opts.app_label}.delete_{run_opts.model_name}"):
            perms.discard(run_opts.verbose_name)
        field = "workspace_id" if self.model is Workspace else "preset_id"
        active = WorkspaceTaskRun.objects.using(router.db_for_write(self.model)).filter(
            **{f"{field}__in": [obj.pk for obj in objs]},
            status__in=(WorkspaceTaskRun.Status.REQUESTED, WorkspaceTaskRun.Status.RUNNING),
        )
        protected.extend(
            f"Task run #{pk} ({status})" for pk, status in active.values_list("pk", "status")
        )
        return deleted, counts, perms, protected

    def deletion_blocked(self, request):
        self.message_user(
            request,
            "Deletion blocked: active task runs must finish or be explicitly recovered first.",
            messages.ERROR,
        )
        return HttpResponseRedirect(
            reverse(f"admin:{self.opts.app_label}_{self.opts.model_name}_changelist")
        )

    def delete_view(self, request, object_id, extra_context=None):
        try:
            return super().delete_view(request, object_id, extra_context)
        except ProtectedError:
            return self.deletion_blocked(request)

    def changelist_view(self, request, extra_context=None):
        if request.method != "POST":
            return super().changelist_view(request, extra_context)
        try:
            # Roll back admin deletion logs too if a new request races confirmation.
            with transaction.atomic(using=router.db_for_write(self.model)):
                return super().changelist_view(request, extra_context)
        except ProtectedError:
            return self.deletion_blocked(request)


@admin.register(Workspace)
class WorkspaceAdmin(TaskParentAdmin):
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
class TaskPresetAdmin(TaskParentAdmin):
    list_display = ("label", "command", "category", "order")
    list_filter = ("category",)
    search_fields = ("label", "command")


@admin.register(WorkspaceTaskRun)
class WorkspaceTaskRunAdmin(admin.ModelAdmin):
    list_display = ("workspace", "preset", "status", "progress", "created_at")
    list_filter = ("status", "preset")
    search_fields = ("workspace__name", "preset__label")

    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in self.model._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(DocLink)
class DocLinkAdmin(admin.ModelAdmin):
    list_display = ("title", "stage", "url")
    list_filter = ("stage", "category")
    search_fields = ("title", "url")
