import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse

from djdesk.inspector import assets, data_lab
from djdesk.inspector.models import TaskPreset, Workspace, WorkspaceTaskRun
from djdesk.inspector.views import OfflineDocsView


class AdmittedAssetTests(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "bundle"
        self.root.mkdir()
        (self.root / "index.html").write_text("admitted")
        self.settings_override = override_settings(INSPECTOR_DOCS_BUNDLE_ROOT=self.root)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)

    def test_replacement_after_resolution_cannot_serve_outside_file(self):
        outside = Path(self.temp.name) / "private.html"
        outside.write_text("synthetic outside secret")
        resolve = assets.resolve_docs_asset

        def replace(resource):
            path = resolve(resource)
            path.unlink()
            path.symlink_to(outside)
            return path

        with mock.patch.object(assets, "resolve_docs_asset", side_effect=replace):
            response = self.client.get(reverse("inspector:docs-offline-root"))
        try:
            self.assertEqual(response.status_code, 404)
        finally:
            response.close()

    def test_parent_replacement_after_resolution_cannot_serve_outside_file(self):
        child = self.root / "guide"
        child.mkdir()
        (child / "page.html").write_text("admitted page")
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        (outside / "page.html").write_text("synthetic outside secret")
        resolve = assets.resolve_docs_asset

        def replace(resource):
            path = resolve(resource)
            child.rename(self.root / "old-guide")
            child.symlink_to(outside, target_is_directory=True)
            return path

        with mock.patch.object(assets, "resolve_docs_asset", side_effect=replace):
            response = self.client.get(reverse("inspector:docs-offline", args=["guide/page.html"]))
        try:
            self.assertEqual(response.status_code, 404)
        finally:
            response.close()

    def test_response_owns_handle_and_constructor_failure_closes_it(self):
        handles = []
        opener = assets.open_docs_asset

        def capture_asset(resource):
            path, handle = opener(resource)
            handles.append(handle)
            return path, handle

        with mock.patch.object(assets, "open_docs_asset", side_effect=capture_asset):
            response = OfflineDocsView.as_view()(RequestFactory().get("/docs/offline/"))
            self.assertFalse(handles[-1].closed)
            response.close()
            self.assertTrue(handles[-1].closed)
            with mock.patch(
                "djdesk.inspector.views.FileResponse", side_effect=RuntimeError("fail")
            ):
                with self.assertRaises(RuntimeError):
                    OfflineDocsView.as_view()(RequestFactory().get("/docs/offline/"))
            try:
                self.assertTrue(handles[-1].closed)
            finally:
                handles[-1].close()

    def test_bundle_generation_replacement_is_refused(self):
        resolve = assets.resolve_docs_asset

        def replace(resource):
            path = resolve(resource)
            self.root.rename(self.root.with_name("old-bundle"))
            self.root.mkdir()
            (self.root / "index.html").write_text("replacement")
            return path

        with mock.patch.object(assets, "resolve_docs_asset", side_effect=replace):
            response = self.client.get(reverse("inspector:docs-offline-root"))
        self.assertEqual(response.status_code, 404)

    def test_admitted_descriptor_survives_later_bundle_replacement(self):
        _, handle = assets.open_docs_asset("")
        with handle:
            self.root.rename(self.root.with_name("old-bundle"))
            self.root.mkdir()
            (self.root / "index.html").write_text("replacement")
            self.assertEqual(handle.read(), b"admitted")

    def test_fifo_replacing_leaf_during_open_never_blocks(self):
        original_open = os.open

        def replace(path, flags, *args, **kwargs):
            if path == "index.html":
                self.assertTrue(flags & os.O_NONBLOCK)
                (self.root / "index.html").unlink()
                os.mkfifo(self.root / "index.html")
            return original_open(path, flags, *args, **kwargs)

        with mock.patch.object(os, "open", side_effect=replace) as patched_open:
            with mock.patch.object(os, "supports_dir_fd", os.supports_dir_fd | {patched_open}):
                response = self.client.get(reverse("inspector:docs-offline-root"))
        self.assertEqual(response.status_code, 404)

    def test_portable_reader_refuses_symlink_replacement(self):
        outside = Path(self.temp.name) / "outside.html"
        outside.write_text("synthetic secret")
        original_open = os.open

        def replace(path, *args, **kwargs):
            (self.root / "index.html").unlink()
            (self.root / "index.html").symlink_to(outside)
            return original_open(path, *args, **kwargs)

        with (
            mock.patch.object(os, "supports_dir_fd", set()),
            mock.patch.object(os, "open", side_effect=replace),
        ):
            response = self.client.get(reverse("inspector:docs-offline-root"))
        self.assertEqual(response.status_code, 404)

    def test_offline_assets_do_not_inherit_dynamic_cache_policy(self):
        response = self.client.get(reverse("inspector:docs-offline-root"))
        try:
            self.assertNotIn("no-store", response.headers.get("Cache-Control", ""))
        finally:
            response.close()


class PrivateInspectorResponsesTests(TestCase):
    def test_csrf_refusal_and_server_errors_receive_the_same_policy(self):
        response = Client(enforce_csrf_checks=True).post(reverse("inspector:task-run-create"))
        self.assertEqual(response.status_code, 403)
        self.assertIn("no-store", response.headers.get("Cache-Control", ""))
        workspace = Workspace.objects.create(name="Synthetic error", project_path="/tmp/error")
        with mock.patch(
            "djdesk.inspector.views.workspace_status_payload", side_effect=RuntimeError
        ):
            response = Client(raise_request_exception=False).get(
                reverse("inspector:workspace-status", args=[workspace.slug])
            )
        self.assertEqual(response.status_code, 500)
        self.assertIn("no-store", response.headers.get("Cache-Control", ""))

    def test_success_and_error_responses_are_private_no_store(self):
        with TemporaryDirectory() as root, override_settings(INSPECTOR_DATA_LAB_ROOT=root):
            workspace = Workspace.objects.create(name="Synthetic", project_path="/tmp/synthetic")
            preset = TaskPreset.objects.first()
            run = WorkspaceTaskRun.objects.create(workspace=workspace, preset=preset)
            notebook = data_lab.export_notebook(workspace, "schema-audit")
            cases = [
                ("get", reverse("inspector:dashboard"), {}, 200),
                ("get", reverse("inspector:wizard"), {}, 200),
                ("get", reverse("inspector:workspace-status", args=[workspace.slug]), {}, 200),
                ("get", reverse("inspector:workspace-status", args=["missing"]), {}, 404),
                ("get", reverse("inspector:task-run-detail", args=[run.pk]), {}, 200),
                ("get", reverse("inspector:task-run-detail", args=[999999]), {}, 404),
                ("post", reverse("inspector:task-run-create"), {}, 400),
                ("post", reverse("inspector:data-lab-export", args=[workspace.slug]), {}, 400),
                ("get", reverse("inspector:data-lab-export", args=[workspace.slug]), {}, 405),
                (
                    "get",
                    reverse("inspector:data-lab-notebook", args=[workspace.slug, notebook.stem]),
                    {},
                    200,
                ),
                (
                    "get",
                    reverse("inspector:data-lab-notebook", args=[workspace.slug, "missing"]),
                    {},
                    404,
                ),
            ]
            for method, url, data, status in cases:
                with self.subTest(method=method, url=url):
                    response = getattr(self.client, method)(url, data)
                    self.assertEqual(response.status_code, status)
                    self.assertIn("no-store", response.headers.get("Cache-Control", ""))
                    self.assertIn("private", response["Cache-Control"])
            notebook.write_text("invalid json")
            response = self.client.get(
                reverse("inspector:data-lab-notebook", args=[workspace.slug, notebook.stem])
            )
            self.assertEqual(response.status_code, 422)
            self.assertIn("no-store", response.headers.get("Cache-Control", ""))
