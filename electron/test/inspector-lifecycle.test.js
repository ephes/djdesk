const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const source = readFileSync(path.join(__dirname,
  '../../src/djdesk/inspector/static/inspector/app.js'), 'utf8');
const flush = () => new Promise((resolve) => setImmediate(resolve));
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function harness({ respectAbort = true } = {}) {
  const requests = [], timers = new Map();
  let timerId = 0;
  const clock = {
    setTimeout(fn, delay) { const id = ++timerId; timers.set(id, { fn, delay }); return id; },
    clearTimeout(id) { timers.delete(id); },
    setInterval(fn, delay) { const id = ++timerId; timers.set(id, { fn, delay }); return id; },
  };
  const InspectorUI = vm.runInNewContext(source + '\nInspectorUI;', {
    document: { addEventListener() {} }, window: clock, clearTimeout: clock.clearTimeout,
    AbortController, FormData: class {},
    fetch(url, options) {
      const request = { url, options, ...deferred() };
      requests.push(request);
      if (respectAbort) options.signal?.addEventListener('abort', () => request.reject(new Error('aborted')));
      return request.promise;
    },
  });
  const ui = Object.create(InspectorUI.prototype);
  const payloads = [], connections = [], errors = [];
  Object.assign(ui, {
    updateFromPayload(payload) { payloads.push(payload); connections.push(true); },
    toggleConnection(value) { connections.push(value); },
    showToast(message) { errors.push(message); }, getCsrfToken() { return 'synthetic'; },
    formatErrors() { return 'Refused'; },
  });
  function fire(delay) {
    const entry = [...timers].find(([, timer]) => timer.delay === delay);
    assert.ok(entry, `timer for ${delay}ms exists`);
    timers.delete(entry[0]); entry[1].fn();
  }
  return { ui, requests, timers, fire, payloads, connections, errors };
}

test('task submission owns one request, preserves input on failure and unlocks retry', async () => {
  const h = harness();
  const buttons = [{ disabled: false }, { disabled: true }];
  let submit, resets = 0;
  const form = {
    querySelectorAll() { return buttons; },
    addEventListener(name, handler) { assert.equal(name, 'submit'); submit = handler; },
    reset() { resets++; },
  };
  h.ui.root = { querySelector() { return form; } };
  h.ui.taskEndpoint = '/tasks/';
  h.ui.bindTaskForm();
  const event = { preventDefault() {} };
  const first = submit(event);
  submit(event);
  assert.equal(h.requests.length, 1);
  assert.equal(buttons[0].disabled, true);
  h.requests[0].reject(new Error('offline'));
  await first; await flush();
  assert.equal(resets, 0);
  assert.equal(buttons[0].disabled, false);
  assert.equal(buttons[1].disabled, true);
  const retry = submit(event);
  assert.equal(h.requests.length, 2);
  h.requests[1].resolve({ ok: true, json: async () => ({ tasks: ['complete'] }) });
  await retry; await flush();
  assert.equal(resets, 1);
  assert.equal(h.payloads.length, 1);
  assert.equal(buttons[0].disabled, false);
});

test('status polling waits for completion and preserves good evidence on HTTP failure', async () => {
  const h = harness();
  h.ui.statusUrl = '/status/';
  h.ui.startPolling();
  assert.equal(h.requests.length, 1);
  assert.equal([...h.timers.values()].filter((t) => t.delay === 6000).length, 0);
  await flush();
  assert.equal(h.requests.length, 1);
  const complete = { tasks: [{ status: 'succeeded' }] };
  h.requests[0].resolve({ ok: true, json: async () => complete });
  await flush();
  assert.equal(h.payloads[0], complete);
  h.fire(6000);
  h.requests[1].resolve({ ok: false, json: async () => ({ error: 'server failure' }) });
  await flush();
  assert.deepEqual(h.payloads, [complete]);
  assert.deepEqual(h.connections, [true, false]);
  assert.equal(h.timers.size, 1);
});

test('a stalled status request is aborted before scheduling the next request', async () => {
  const h = harness();
  h.ui.statusUrl = '/status/'; h.ui.startPolling();
  h.fire(10000); await flush();
  assert.equal(h.requests[0].options.signal.aborted, true);
  assert.deepEqual(h.connections, [false]);
  assert.equal(h.payloads.length, 0);
  h.fire(6000);
  assert.equal(h.requests.length, 2);
});

function drawerHarness() {
  const h = harness({ respectAbort: false });
  const visible = new Set();
  h.ui.taskDrawer = { classList: {
    add(value) { visible.add(value); }, remove(value) { visible.delete(value); },
  } };
  h.ui.taskDetailTemplate = '/tasks/0/';
  h.ui.setTaskDrawerLoading = () => {};
  h.ui.renderTaskDrawer = (data) => h.payloads.push(data);
  return { ...h, visible };
}

test('drawer ignores old task successes, failures and timers after switching', async () => {
  for (const failure of [false, true]) {
    const h = drawerHarness();
    h.ui.openTaskDrawer('A');
    h.ui.openTaskDrawer('B');
    assert.equal(h.requests[0].options.signal.aborted, true);
    h.requests[1].resolve({ ok: true, json: async () => ({ label: 'B', status: 'running' }) });
    await flush();
    if (failure) h.requests[0].reject(new Error('late failure'));
    else h.requests[0].resolve({ ok: true, json: async () => ({ label: 'A', status: 'running' }) });
    await flush();
    assert.deepEqual(h.payloads.map((p) => p.label), ['B']);
    assert.equal(h.errors.length, 0);
    assert.equal(h.visible.has('is-visible'), true);
    assert.equal(h.timers.size, 1);
    h.fire(3000);
    assert.equal(h.requests[2].url, '/tasks/B/');
  }
});

test('closing invalidates pending drawer requests and already queued refresh callbacks', async () => {
  const h = drawerHarness();
  h.ui.openTaskDrawer('A');
  h.ui.closeTaskDrawer();
  assert.equal(h.requests[0].options.signal.aborted, true);
  h.requests[0].resolve({ ok: true, json: async () => ({ label: 'A', status: 'running' }) });
  await flush();
  assert.equal(h.payloads.length, 0);
  assert.equal(h.timers.size, 0);
  h.ui.openTaskDrawer('B');
  h.requests[1].resolve({ ok: true, json: async () => ({ label: 'B', status: 'running' }) });
  await flush();
  const queued = [...h.timers.values()][0].fn;
  h.ui.closeTaskDrawer(); queued();
  assert.equal(h.requests.length, 2);
  assert.equal(h.timers.size, 0);
  assert.equal(h.visible.has('is-visible'), false);
});
