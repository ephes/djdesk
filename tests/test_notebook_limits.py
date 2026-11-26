import json
from collections import Counter
from tempfile import TemporaryDirectory
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse

from djdesk.inspector import data_lab
from djdesk.inspector.models import Workspace


class NotebookViewLimitTests(TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.enterContext(override_settings(INSPECTOR_DATA_LAB_ROOT=temporary.name))
        self.workspace = Workspace.objects.create(name="Limits", project_path="/synthetic/limits")
        self.path = data_lab.workspace_data_lab_dir(self.workspace) / "edited.ipynb"
        self.path.parent.mkdir()
        self.url = reverse("inspector:data-lab-notebook", args=[self.workspace.slug, "edited"])

    def write_cells(self, cells):
        content = json.dumps({"cells": cells}).encode()
        self.path.write_bytes(content)
        return content

    def assert_refused_before_render(self, cells):
        content = self.write_cells(cells)
        with mock.patch.object(data_lab, "render_notebook_html") as render:
            response = self.client.get(self.url)
        self.assertContains(response, "supported viewer size", status_code=422)
        render.assert_not_called()
        self.assertEqual(content, self.path.read_bytes())

    def test_tiny_cells_and_outputs_have_count_limits(self):
        self.assert_refused_before_render([{"cell_type": "markdown", "source": []}] * 501)
        self.assert_refused_before_render(
            [{"cell_type": "code", "source": [], "outputs": [{"text": []}] * 51}]
        )

    def test_aggregate_text_and_escaped_html_are_admitted_before_render(self):
        with mock.patch.object(data_lab, "NOTEBOOK_VIEW_TEXT_BYTES", 8):
            self.assert_refused_before_render(
                [
                    {"cell_type": "markdown", "source": ["éé"]},
                    {"cell_type": "code", "source": "x", "outputs": [{"text": "éé"}]},
                ]
            )
        with mock.patch.object(data_lab, "NOTEBOOK_VIEW_HTML_BYTES", 128):
            # Text fits; escaping would amplify it beyond the HTML envelope.
            self.assert_refused_before_render([{"cell_type": "code", "source": "&" * 20}])

    def test_exact_cell_and_output_limits_render(self):
        self.write_cells([{"cell_type": "markdown", "source": []}] * 500)
        self.assertContains(
            self.client.get(self.url), "<section class='nb-cell nb-cell--markdown'>", count=500
        )
        self.write_cells([{"cell_type": "code", "source": [], "outputs": [{"text": "ok"}] * 50}])
        self.assertContains(self.client.get(self.url), "<pre>ok</pre>", count=50)

    def test_exact_text_and_html_budgets_render(self):
        self.write_cells([{"cell_type": "code", "source": "éé"}])
        with mock.patch.object(data_lab, "NOTEBOOK_VIEW_TEXT_BYTES", 4):
            self.assertContains(self.client.get(self.url), "éé")
        self.write_cells([{"cell_type": "code", "source": "&"}])
        # Code-cell wrapper plus five escaped bytes, with no output block.
        limit = len(
            "<section class='nb-cell nb-cell--code'><pre><code>&amp;</code></pre></section>"
        )
        with mock.patch.object(data_lab, "NOTEBOOK_VIEW_HTML_BYTES", limit):
            self.assertContains(self.client.get(self.url), "&amp;")
        with mock.patch.object(data_lab, "NOTEBOOK_VIEW_HTML_BYTES", limit - 1):
            self.assert_refused_before_render([{"cell_type": "code", "source": "&"}])

    def test_code_source_and_output_lines_are_encoded_once_during_admission(self):
        encodings = Counter()

        class TrackedLine(str):
            def encode(self, encoding="utf-8", errors="strict"):
                encodings[self] += 1
                return super().encode(encoding, errors)

        source = [TrackedLine(f"source {index} &é\n") for index in range(256)]
        outputs = [
            {"text": [TrackedLine(f"output {index}:{line} <é>\n") for line in range(64)]}
            for index in range(4)
        ]
        data_lab._admit_notebook_view(
            {"cells": [{"cell_type": "code", "source": source, "outputs": outputs}]}
        )

        self.assertEqual(len(encodings), 512)
        self.assertEqual(set(encodings.values()), {1})

    def test_malformed_text_in_an_oversized_field_is_still_a_shape_error(self):
        for text in (["é", "\ud800"], ["é", 1]):
            for cell, message in (
                ({"cell_type": "markdown", "source": text}, "Notebook cell source"),
                ({"cell_type": "code", "source": text}, "Notebook cell source"),
                ({"cell_type": "code", "outputs": [{"text": text}]}, "Notebook outputs"),
            ):
                with (
                    self.subTest(cell=cell),
                    mock.patch.object(data_lab, "NOTEBOOK_VIEW_TEXT_BYTES", 1),
                    self.assertRaisesRegex(data_lab.InvalidNotebookError, message),
                ):
                    data_lab._admit_notebook_view({"cells": [cell]})

    def test_markdown_line_endings_render_and_use_the_same_html_budget(self):
        normalized = "First line\ncontinues\n\nSecond & last"
        sources = (
            normalized,
            normalized.replace("\n", "\r\n"),
            ["First line\r", "\ncontinues\n", "\r\nSecond & last"],
            normalized.replace("\n", "\r"),
        )
        wrapper = "<section class='nb-cell nb-cell--markdown'></section>"
        limit = len(wrapper) + len(normalized.replace("&", "&amp;")) + 4 * len("<p></p>")
        for source in sources:
            with self.subTest(source=source):
                cells = [{"cell_type": "markdown", "source": source}]
                original = self.write_cells(cells)
                with mock.patch.object(data_lab, "NOTEBOOK_VIEW_HTML_BYTES", limit):
                    self.assertContains(
                        self.client.get(self.url),
                        "<p>First line\ncontinues</p><p>Second &amp; last</p>",
                    )
                self.assertEqual(self.path.read_bytes(), original)
                with mock.patch.object(data_lab, "NOTEBOOK_VIEW_HTML_BYTES", limit - 1):
                    self.assert_refused_before_render(cells)

    def test_crlf_source_still_counts_toward_text_limit_and_code_is_preserved(self):
        source = "x\r\n\r\ny"
        with mock.patch.object(data_lab, "NOTEBOOK_VIEW_TEXT_BYTES", len(source) - 1):
            self.assert_refused_before_render([{"cell_type": "markdown", "source": source}])
        self.write_cells([{"cell_type": "code", "source": source}])
        self.assertContains(self.client.get(self.url), f"<pre><code>{source}</code></pre>")

    def test_file_byte_limit_precedes_json_decoding(self):
        content = self.write_cells([])
        with mock.patch.object(data_lab, "NOTEBOOK_FILE_BYTES", len(content)):
            self.assertEqual(self.client.get(self.url).status_code, 200)
            self.path.write_bytes(content + b" ")
            with mock.patch.object(data_lab.json, "loads", wraps=json.loads) as decode:
                with self.assertRaises(data_lab.NotebookSizeError):
                    data_lab.load_notebook(self.workspace, "edited")
            decode.assert_not_called()
            self.assertContains(self.client.get(self.url), "supported viewer size", status_code=422)
            self.assertEqual(self.path.read_bytes(), content + b" ")
