const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const source = readFileSync(path.join(__dirname,
  '../../src/djdesk/inspector/static/inspector/app.js'), 'utf8');
const InspectorUI = vm.runInNewContext(source + '\nInspectorUI;', {
  document: { addEventListener() {} },
});

test('reexport opens the newly selected notebook while retaining the old listing', () => {
  const opened = [];
  const ui = {
    activeNotebookSlug: 'schema-audit',
    updateTemplateOptions() {}, renderDataLabList() {}, highlightNotebook() {},
    openNotebook(url, slug) { opened.push([url, slug]); this.activeNotebookSlug = slug; },
  };
  InspectorUI.prototype.updateDataLab.call(ui, {
    templates: [], selected_notebook: 'schema-audit-3',
    notebooks: [
      { slug: 'schema-audit', viewer_url: '/old' },
      { slug: 'schema-audit-3', viewer_url: '/new' },
    ],
  });
  assert.deepEqual(opened, [['/new', 'schema-audit-3']]);
  assert.equal(ui.activeNotebookSlug, 'schema-audit-3');
});
