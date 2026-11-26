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
  const timers = new Map();
  const Class = vm.runInNewContext(source + '\nInspectorUI;', {
    document: { addEventListener() {} },
    AbortController, TextDecoder, Uint8Array,
    window: {
      setTimeout(callback, delay) { const id = {}; timers.set(id, { callback, delay }); return id; },
      clearTimeout(id) { timers.delete(id); },
    },
    FormData: class { get() { return 'schema-audit'; } },
    fetch: (url, options) => new Promise((resolve, reject) => requests.push({ resolve, reject, options })),
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
  const toasts = [];
  ui.showToast = (message, level) => { toasts.push({ message, level }); };
  ui.statusUpdates = 0;
  ui.updateFromPayload = () => { ui.statusUpdates++; ui.activeNotebookSlug = 'stale'; };
  ui.updateDataLab = (data) => { ui.activeNotebookSlug = data.selected_notebook; };
  ui.openNotebook = (url, slug) => { ui.activeNotebookSlug = slug; };
  ui.bindDataLab();
  return { ui, requests, controls, handlers, timers, toasts,
    submit: () => handlers.submit({ preventDefault() {} }) };
}

const settled = () => new Promise((resolve) => setImmediate(resolve));
const jsonResponse = (payload, status = 201) => new Response(JSON.stringify(payload), { status });

for (const status of [201, 400]) {
  for (const length of [undefined, '1', '1000']) {
    test(`export cancels oversized ${status} reply (Content-Length ${length}) before parsing`, async () => {
      const { ui, requests, controls, submit, toasts } = exportFixture();
      ui.responseBytes = 64;
      let cancelled = false;
      let parsed = false;
      const response = new Response(new ReadableStream({
        start(controller) {
          controller.enqueue(new Uint8Array(64));
          controller.enqueue(new Uint8Array(1));
        },
        cancel() { cancelled = true; },
      }), { status, headers: length === undefined ? {} : { 'Content-Length': length } });
      response.json = async () => { parsed = true; return { data_lab: { selected_notebook: 'oversized' } }; };
      const pending = submit();
      requests[0].resolve(response);
      await pending;
      assert.equal(cancelled, true);
      assert.equal(parsed, false);
      assert.equal(response.body.locked, false);
      assert.equal(ui.activeNotebookSlug, 'schema-audit');
      assert.deepEqual(controls.map((control) => control.disabled), [false, false, true]);
      assert.match(toasts[0].message, /byte limit.*may already have completed.*inspect.*retry/i);
      assert.equal(requests.length, 1);
      const retry = submit();
      requests[1].resolve(jsonResponse({ data_lab: { selected_notebook: 'small' } }));
      await retry;
      assert.equal(ui.activeNotebookSlug, 'small');
    });
  }
}

test('export accepts exact byte limit with split Unicode and retains bounded HTTP diagnostics', async () => {
  const { ui, requests, submit, toasts } = exportFixture();
  const bytes = new TextEncoder().encode(JSON.stringify({ data_lab: { selected_notebook: 'é€' } }));
  ui.responseBytes = bytes.length;
  const pending = submit();
  const response = new Response(new ReadableStream({ start(controller) {
    for (const byte of bytes) controller.enqueue(Uint8Array.of(byte));
    controller.close();
  } }));
  response.json = () => { assert.fail('export must use the bounded reader'); };
  requests[0].resolve(response);
  await pending;
  assert.equal(ui.activeNotebookSlug, 'é€');
  ui.responseBytes = 1024;
  const refused = submit();
  requests[1].resolve(jsonResponse({ errors: { template: ['Synthetic refusal'] } }, 400));
  await refused;
  assert.match(toasts.at(-1).message, /Synthetic refusal/);
  assert.doesNotMatch(toasts.at(-1).message, /may already have completed/, 'a 4xx refusal is definite');
  assert.equal(ui.activeNotebookSlug, 'é€');
  const durability = submit();
  requests[2].resolve(jsonResponse({ errors: { template: ['Synthetic durability'] } }, 500));
  await durability;
  assert.match(toasts.at(-1).message, /Synthetic durability.*may already have completed/);
});

for (const stage of ['headers', 'body']) {
  test(`export total deadline releases a stalled ${stage} owner and fences late success`, async () => {
    const { ui, requests, controls, submit, timers, toasts } = exportFixture();
    const pending = submit();
    let completeBody;
    if (stage === 'body') {
      requests[0].resolve(new Response(new ReadableStream({ start(controller) {
        completeBody = (payload) => {
          controller.enqueue(new TextEncoder().encode(JSON.stringify(payload)));
          controller.close();
        };
      } })));
      await settled();
    }
    assert.equal(timers.size, 1, 'one total request/body deadline');
    const [timer] = timers.values();
    assert.equal(timer.delay, 30000);
    timer.callback();
    await pending;
    assert.equal(requests[0].options.signal.aborted, true);
    assert.equal(ui.dataLabExportPending, false);
    assert.deepEqual(controls.map((control) => control.disabled), [false, false, true]);
    assert.match(toasts[0].message, /may already have completed/i);
    assert.match(toasts[0].message, /inspect.*retry/i);
    assert.equal(timers.size, 0);
    assert.equal(requests.length, 1, 'uncertain mutation is never replayed');

    // A deliberate retry may finish before the obsolete request settles.
    const retry = submit();
    requests[1].resolve(jsonResponse({
      data_lab: { selected_notebook: 'deliberate-retry' },
    }));
    await retry;
    const late = { data_lab: { selected_notebook: 'obsolete' } };
    if (stage === 'body') completeBody(late);
    else requests[0].resolve(jsonResponse(late));
    await settled();
    assert.equal(ui.activeNotebookSlug, 'deliberate-retry');
    assert.equal(toasts.filter((toast) => toast.level === 'success').length, 1);
    assert.equal(timers.size, 0);
  });
}

test('export owns one POST and a later manual notebook choice fences its response', async () => {
  const { ui, requests, controls, handlers, submit } = exportFixture();
  submit();
  submit();
  assert.equal(requests.length, 1);
  assert.equal(controls[0].disabled, true);
  handlers.click({ target: { closest: () => ({ dataset: {
    notebookUrl: '/manual', notebookSlug: 'log-study',
  } }) } });
  requests[0].resolve(jsonResponse({
    data_lab: { selected_notebook: 'schema-audit-2', notebooks: [] },
  }));
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
    else request.resolve(kind === 'json' ? new Response('invalid JSON')
      : jsonResponse({ errors: { template: ['Synthetic refusal'] } }, 400));
    await settled();
    assert.equal(controls[0].disabled, false);
  }
  assert.equal(requests.length, 3);
  submit();
  requests.at(-1).resolve(jsonResponse({
    data_lab: { selected_notebook: 'schema-audit-9' },
  }));
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
  ui.dataLabNotebooks = new Map();
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

test('incomplete inventory polls keep known notebooks until a complete inventory confirms removal', () => {
  const ui = Object.create(InspectorUI.prototype);
  ui.dataLabIncomplete = { hidden: true };
  ui.renderDataLabList = (notebooks) => { ui.visible = Array.from(notebooks); };
  ui.highlightNotebook = () => {};
  ui.openNotebook = (url, slug) => { ui.activeNotebookSlug = slug; };
  const known = { slug: 'known', title: 'Known', viewer_url: '/known' };
  const removed = { slug: 'removed', title: 'Removed', viewer_url: '/removed' };
  const added = { slug: 'added', title: 'Added', viewer_url: '/added' };
  const updated = { ...known, title: 'Updated' };

  ui.updateDataLab({ notebooks: [known, removed], notebooks_incomplete: false });
  ui.updateDataLab({ notebooks: [], notebooks_incomplete: true });
  assert.deepEqual(ui.visible, [known, removed]);
  assert.equal(ui.dataLabIncomplete.hidden, false);

  ui.updateDataLab({ notebooks: [added, updated], notebooks_incomplete: true });
  assert.deepEqual(ui.visible, [updated, removed, added]);
  ui.updateDataLab({ notebooks: [], notebooks_incomplete: true });
  assert.deepEqual(ui.visible, [updated, removed, added]);

  ui.updateDataLab({ notebooks: [added, updated], notebooks_incomplete: false });
  assert.deepEqual(ui.visible, [added, updated]);
  assert.equal(ui.dataLabIncomplete.hidden, true);
  ui.updateDataLab({ notebooks: [], notebooks_incomplete: false });
  assert.deepEqual(ui.visible, []);
  assert.equal(ui.activeNotebookSlug, 'known', 'inventory updates keep the open viewer');
});

test('a minimal committed receipt opens its file and retains optional inventory state', () => {
  const ui = Object.create(InspectorUI.prototype);
  ui.dataLabIncomplete = { hidden: false };
  ui.updateTemplateOptions = () => { assert.fail('receipt must retain templates'); };
  ui.renderDataLabList = () => { assert.fail('receipt must retain inventory'); };
  ui.openNotebook = (url, slug) => { ui.activeNotebookSlug = slug; ui.url = url; };
  ui.updateDataLab({ selected_notebook: 'schema-audit-2', selected_notebook_url: '/published-2' });
  assert.equal(ui.activeNotebookSlug, 'schema-audit-2');
  assert.equal(ui.url, '/published-2');
  assert.equal(ui.dataLabIncomplete.hidden, false);
});
