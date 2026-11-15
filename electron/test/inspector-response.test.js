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

function response(chunks, length) {
  let cancelled = false;
  const body = new ReadableStream({
    start(controller) { for (const chunk of chunks) controller.enqueue(chunk); },
    cancel() { cancelled = true; },
  });
  return { value: new Response(body, { headers: length === undefined ? {} : { 'Content-Length': length } }),
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
  let poll;
  let next;
  let parses = 0;
  const { InspectorUI } = load({
    fetch: async () => next,
    window: { setInterval(callback) { poll = callback; } },
  });
  const ui = Object.create(InspectorUI.prototype);
  ui.statusUrl = '/status';
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
  poll();
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
