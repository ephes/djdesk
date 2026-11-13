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

function exportFixture() {
  const requests = [];
  const Class = vm.runInNewContext(source + '\nInspectorUI;', {
    document: { addEventListener() {} },
    FormData: class { get() { return 'schema-audit'; } },
    fetch: () => new Promise((resolve, reject) => requests.push({ resolve, reject })),
  });
  const ui = Object.create(Class.prototype);
  const handlers = {};
  const controls = [{ disabled: false }, { disabled: false }, { disabled: true }];
  ui.dataLabForm = {
    addEventListener(event, callback) { handlers[event] = callback; },
    querySelectorAll() { return controls; },
  };
  ui.dataLabList = { addEventListener(event, callback) { handlers.click = callback; } };
  ui.dataLabExportUrl = '/export';
  ui.dataLabExportPending = false;
  ui.notebookSelectionGeneration = 0;
  ui.activeNotebookSlug = 'schema-audit';
  ui.getCsrfToken = () => '';
  ui.showToast = () => {};
  ui.statusUpdates = 0;
  ui.updateFromPayload = () => { ui.statusUpdates++; ui.activeNotebookSlug = 'stale'; };
  ui.updateDataLab = (data) => { ui.activeNotebookSlug = data.selected_notebook; };
  ui.openNotebook = (url, slug) => { ui.activeNotebookSlug = slug; };
  ui.bindDataLab();
  return { ui, requests, controls, handlers,
    submit: () => handlers.submit({ preventDefault() {} }) };
}

const settled = () => new Promise((resolve) => setImmediate(resolve));

test('export owns one POST and a later manual notebook choice fences its response', async () => {
  const { ui, requests, controls, handlers, submit } = exportFixture();
  submit();
  submit();
  assert.equal(requests.length, 1);
  assert.equal(controls[0].disabled, true);
  handlers.click({ target: { closest: () => ({ dataset: {
    notebookUrl: '/manual', notebookSlug: 'log-study',
  } }) } });
  requests[0].resolve({ ok: true, json: async () => ({
    data_lab: { selected_notebook: 'schema-audit-2', notebooks: [] },
  }) });
  await settled();
  assert.equal(ui.activeNotebookSlug, 'log-study');
  assert.equal(ui.statusUpdates, 0, 'export snapshot must not overwrite polled workspace state');
  assert.deepEqual(controls.map((control) => control.disabled), [false, false, true]);
});

test('export failures restore controls and permit deliberate retry without automatic replay', async () => {
  const { ui, requests, controls, submit } = exportFixture();
  for (const kind of ['network', 'http', 'json']) {
    submit();
    const request = requests.at(-1);
    if (kind === 'network') request.reject(new Error('Synthetic network failure'));
    else request.resolve({ ok: kind !== 'http', json: async () => {
      if (kind === 'json') throw new Error('Synthetic invalid JSON');
      return { errors: { template: ['Synthetic refusal'] } };
    } });
    await settled();
    assert.equal(controls[0].disabled, false);
  }
  assert.equal(requests.length, 3);
  submit();
  requests.at(-1).resolve({ ok: true, json: async () => ({
    data_lab: { selected_notebook: 'schema-audit-9' },
  }) });
  await settled();
  assert.equal(ui.activeNotebookSlug, 'schema-audit-9');
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

test('an exported notebook outside the window opens directly and older polls retain it', () => {
  const ui = Object.create(InspectorUI.prototype);
  ui.activeNotebookSlug = 'log-study';
  ui.updateTemplateOptions = () => {};
  ui.renderDataLabList = () => {};
  ui.highlightNotebook = () => {};
  ui.openNotebook = (url, slug) => { ui.activeNotebookSlug = slug; ui.url = url; };
  ui.updateDataLab({ selected_notebook: 'schema-audit-9',
    selected_notebook_url: '/new-9', notebooks: [], notebooks_incomplete: true });
  assert.equal(ui.activeNotebookSlug, 'schema-audit-9');
  assert.equal(ui.url, '/new-9');
  ui.updateDataLab({ notebooks: [{ slug: 'old', viewer_url: '/old' }] });
  ui.updateDataLab({ notebooks: [] });
  assert.equal(ui.activeNotebookSlug, 'schema-audit-9');
});
