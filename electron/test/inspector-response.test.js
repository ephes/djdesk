const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const source = readFileSync(path.join(__dirname,
  '../../src/djdesk/inspector/static/inspector/app.js'), 'utf8');
function load(context = {}) {
  return vm.runInNewContext(source + `\n({ InspectorUI,
    readInspectorJSON: typeof readInspectorJSON === 'function' ? readInspectorJSON : null });`, {
    document: { addEventListener() {} }, TextDecoder, Uint8Array, ...context,
  });
}
const settled = () => new Promise((resolve) => setImmediate(resolve));

function statusPolling(fetch, context = {}) {
  const intervals = new Map();
  const deadlines = new Map();
  let timerId = 0;
  const { InspectorUI } = load({
    fetch, AbortController,
    window: {
      setInterval(callback, delay) {
        assert.equal(delay, 6000);
        intervals.set(++timerId, callback);
        return timerId;
      },
      clearInterval(id) { intervals.delete(id); },
      setTimeout(callback, delay) {
        assert.equal(delay, 30000);
        deadlines.set(++timerId, callback);
        return timerId;
      },
      clearTimeout(id) { deadlines.delete(id); },
    },
    ...context,
  });
  const ui = Object.create(InspectorUI.prototype);
  ui.statusUrl = '/alpha/status';
  const updates = [];
  const connection = [];
  ui.updateFromPayload = (payload) => updates.push(payload.label);
  ui.toggleConnection = (online) => connection.push(online);
  return { ui, intervals, deadlines, updates, connection };
}

test('status ticks coalesce and a workspace switch retires the old request and timer', async () => {
  const requests = [];
  const { ui, intervals, deadlines, updates, connection } = statusPolling((url, { signal }) =>
    new Promise((resolve) => requests.push({ url, signal, resolve })));

  ui.startPolling();
  const [alphaTick] = intervals.values();
  alphaTick();
  alphaTick();
  assert.equal(requests.length, 1);

  ui.statusUrl = '/beta/status';
  ui.startPolling();
  assert.equal(intervals.size, 1);
  assert.equal(requests[0].signal.aborted, true);
  alphaTick();
  assert.deepEqual(requests.map(({ url }) => url), ['/alpha/status', '/beta/status']);
  requests[1].resolve(new Response('{"label":"beta"}'));
  await settled();
  assert.equal(deadlines.size, 0);
  requests[0].resolve(new Response('{"label":"alpha"}'));
  await settled();
  assert.deepEqual(updates, ['beta']);
  assert.deepEqual(connection, []);
});

test('status deadline covers a stalled body and releases the next tick', async () => {
  let finishBody;
  const body = new ReadableStream({
    start(controller) {
      finishBody = () => {
        controller.enqueue(new TextEncoder().encode('{"label":"expired"}'));
        controller.close();
      };
    },
  });
  const signals = [];
  const { ui, intervals, deadlines, updates, connection } = statusPolling(async (url, { signal }) => {
    signals.push(signal);
    return signals.length === 1 ? new Response(body) : new Response('{"label":"recovered"}');
  });

  ui.startPolling();
  await settled();
  assert.equal(body.locked, true);
  assert.equal(deadlines.size, 1);
  for (const expire of deadlines.values()) expire();
  await settled();
  assert.equal(signals[0].aborted, true);
  assert.deepEqual(connection, [false]);
  for (const tick of intervals.values()) tick();
  await settled();
  finishBody();
  await settled();
  assert.deepEqual(updates, ['recovered']);
  assert.equal(deadlines.size, 0);
});

function response(chunks, length, status = 200) {
  let cancelled = false;
  const body = new ReadableStream({
    start(controller) { for (const chunk of chunks) controller.enqueue(chunk); },
    cancel() { cancelled = true; },
  });
  return { value: new Response(body, { status, headers: length === undefined ? {} : { 'Content-Length': length } }),
    cancelled: () => cancelled };
}

for (const length of [undefined, '1', '100']) {
  test(`streamed overflow cancels before parsing (Content-Length ${length})`, async () => {
    const { readInspectorJSON } = load();
    const fixture = response([new Uint8Array(16), new Uint8Array(17)], length);
    await assert.rejects(readInspectorJSON(fixture.value, 32), /byte limit/);
    assert.equal(fixture.cancelled(), true);
    assert.equal(fixture.value.body.locked, false);
  });
}

test('exact-limit split Unicode JSON succeeds; a later small response remains usable', async () => {
  const { readInspectorJSON } = load();
  const bytes = new TextEncoder().encode('{"label":"é€"}');
  const body = new ReadableStream({ start(controller) {
    for (const byte of bytes) controller.enqueue(Uint8Array.of(byte));
    controller.close();
  } });
  assert.equal((await readInspectorJSON(new Response(body), bytes.length)).label, 'é€');
  assert.equal((await readInspectorJSON(new Response('{"ok":true}'))).ok, true);
});

test('status and drawer overflow preserve displayed state and accept the next response', async () => {
  let next;
  let parses = 0;
  const { ui, intervals } = statusPolling(async () => next);
  let displayed = 'last good';
  ui.updateFromPayload = (payload) => { displayed = payload.label; };
  ui.renderTaskDrawer = (payload) => { displayed = payload.label; };
  ui.toggleConnection = () => {};
  ui.getTaskDetailUrl = () => '/task';
  ui.showToast = () => {};
  ui.closeTaskDrawer = () => { displayed = 'closed'; };
  ui.clearTaskDrawerPolling = () => {};
  for (const request of [() => ui.startPolling(), () => ui.loadTaskDetails(1)]) {
    const fixture = response([new Uint8Array(4 * 1024 * 1024), new Uint8Array(4 * 1024 * 1024 + 1)], '1');
    next = fixture.value;
    next.json = async () => { parses++; return { label: 'oversized' }; };
    request();
    await settled();
    assert.equal(fixture.cancelled(), true);
    assert.equal(parses, 0);
    assert.equal(displayed, 'last good');
  }
  next = new Response('{"label":"next good"}');
  for (const tick of intervals.values()) tick();
  await settled();
  assert.equal(displayed, 'next good');
});

test('the dashboard root sets the server-derived response ceiling', () => {
  const { inspectorResponseBytes } = vm.runInNewContext(source + `\n({ inspectorResponseBytes });`, {
    document: { addEventListener() {} }, TextDecoder, Uint8Array,
  });
  assert.equal(inspectorResponseBytes('40000000'), 40000000);
  for (const invalid of [undefined, '', '0', '-1', '1.5', 'x', '1e9', '9'.repeat(20)]) {
    assert.equal(inspectorResponseBytes(invalid), undefined);
  }
});

function taskForm(nextResponse, context = {}) {
  let submit;
  let resets = 0;
  const toasts = [];
  const snapshots = [];
  const form = {
    addEventListener(name, callback) { if (name === 'submit') submit = callback; },
    reset() { resets++; },
  };
  const { InspectorUI } = load({
    fetch: nextResponse,
    FormData: class {},
    AbortController,
    window: { setTimeout() { return 1; }, clearTimeout() {} },
    ...context,
  });
  const ui = Object.create(InspectorUI.prototype);
  ui.root = { querySelector: (selector) => selector === '[data-task-form]' ? form : null };
  ui.taskEndpoint = '/tasks';
  ui.getCsrfToken = () => '';
  ui.showToast = (...args) => toasts.push(args);
  ui.updateFromPayload = (payload) => snapshots.push(payload);
  ui.bindTaskForm();
  return {
    ui, toasts, snapshots, resets: () => resets,
    async submit() {
      submit({ preventDefault() {} });
      await settled();
    },
  };
}

test('a delayed task receipt keeps newer task history and queues a status refresh', async () => {
  const requests = [];
  const drawer = [];
  const button = { addEventListener(name, callback) { this.click = callback; } };
  const receipt = { replaceChildren(...children) { this.children = children; } };
  let resets = 0;
  const form = { reset() { resets++; } };
  const { ui, intervals } = statusPolling((url, options) =>
    new Promise((resolve) => requests.push({ url, method: options.method, resolve })), {
    FormData: class {},
    document: { addEventListener() {}, createElement: () => button },
  });
  ui.taskEndpoint = '/tasks';
  ui.taskHistory = {};
  ui.previousTaskStatuses = new Map();
  ui.escape = String;
  ui.updateFromPayload = (payload) => ui.updateTasks(payload.tasks);
  ui.openTaskDrawer = (id) => drawer.push(id);
  ui.getCsrfToken = () => '';

  const snapshot = (status, progress) => ({
    workspace: 'alpha', tasks: [{ id: 42, label: 'Synthetic task', status, progress }],
  });
  const running = JSON.stringify(snapshot('running', 40));
  ui.startPolling();
  const submitted = ui.submitTaskForm(form, receipt);
  requests[0].resolve(new Response(running));
  await settled();
  assert.match(ui.taskHistory.innerHTML, /task-history-item--running/);
  for (const tick of intervals.values()) tick();

  requests[1].resolve(new Response(JSON.stringify({
    ...snapshot('requested', 0), task_run: { id: 42, status: 'requested' },
  }), { status: 201 }));
  await submitted;
  assert.match(ui.taskHistory.innerHTML, /task-history-item--running/);
  assert.equal(resets, 1);
  assert.equal(receipt.children[0], 'Task #42 submitted. ');
  assert.equal(receipt.children[1].textContent, 'View task log');
  receipt.children[1].click();
  assert.deepEqual(drawer, [42]);
  assert.equal(requests.length, 3, 'refresh waits for the active poll');

  requests[2].resolve(new Response(running));
  await settled();
  assert.deepEqual(requests.map(({ url }) => url),
    ['/alpha/status', '/tasks', '/alpha/status', '/alpha/status']);
  assert.deepEqual(requests.filter(({ method }) => method === 'POST').map(({ url }) => url), ['/tasks']);
  requests[3].resolve(new Response(JSON.stringify(snapshot('running', 75))));
  await settled();
  assert.match(ui.taskHistory.innerHTML, /width:75%/);
});

test('task submissions cancel oversized success and error replies before parsing', async () => {
  for (const status of [201, 400]) {
    const fixture = response([new Uint8Array(16), new Uint8Array(17)], '1', status);
    let parses = 0;
    fixture.value.json = async () => {
      parses++;
      return { workspace: 'synthetic', errors: { __all__: ['Rejected'] } };
    };
    const form = taskForm(async () => fixture.value);
    form.ui.responseBytes = 32;
    await form.submit();
    assert.equal(fixture.cancelled(), true);
    assert.equal(fixture.value.body.locked, false);
    assert.equal(parses, 0);
    assert.deepEqual(form.snapshots, []);
    assert.equal(form.resets(), 0);
    assert.match(form.toasts[0][0], /check task history before retrying/i);
  }
});

test('task submissions distinguish unreadable acknowledgments from structured refusals', async () => {
  let next;
  const form = taskForm(async () => next);
  for (const status of [201, 400]) {
    next = new Response('{"broken":', { status });
    await form.submit();
    assert.match(form.toasts.at(-1)[0], /check task history before retrying/i);
  }
  next = new Response('{"task_run":{"id":"not-a-run-id"}}', { status: 201 });
  await form.submit();
  assert.equal(form.toasts.length, 3);
  assert.match(form.toasts.at(-1)[0], /check task history before retrying/i);
  assert.equal(form.resets(), 0);

  next = new Response('{"errors":{"preset":["Choose a safe preset."]}}', { status: 400 });
  await form.submit();
  assert.deepEqual(form.toasts.at(-1), ['preset: Choose a safe preset.', 'danger']);
  assert.deepEqual(form.snapshots, []);
  assert.equal(form.resets(), 0);

  next = new Response('{"task_run":{"id":42}}', { status: 201 });
  await form.submit();
  assert.deepEqual(form.snapshots, []);
  assert.equal(form.resets(), 1);
});
