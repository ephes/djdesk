const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');
const { pythonExecutable } = require('./build-fixture.cjs');

const source = readFileSync(path.join(__dirname,
  '../../src/djdesk/inspector/static/inspector/app.js'), 'utf8');
const InspectorUI = vm.runInNewContext(source + '\nInspectorUI;', {
  document: {
    addEventListener() {},
    createElement() {
      return {
        textContent: '',
        get innerHTML() {
          return this.textContent.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        },
      };
    },
  },
});

function uiFixture() {
  const ui = Object.create(InspectorUI.prototype);
  for (const key of ['scanBoard', 'insightGrid', 'activityFeed', 'logStream', 'taskHistory',
    'dataLabList']) ui[key] = { innerHTML: '' };
  ui.dataLabForm = { querySelector: () => ui.select };
  ui.select = { innerHTML: '', value: '' };
  ui.previousTaskStatuses = new Map();
  ui.refreshIcons = () => {};
  return ui;
}

function parse(html) {
  const result = spawnSync(pythonExecutable(), ['-c', `
import json, sys
from html.parser import HTMLParser
class Parser(HTMLParser):
    def handle_starttag(self, tag, attrs):
        nodes.append([tag, dict(attrs)])
nodes = []
Parser().feed(sys.stdin.read())
print(json.dumps(nodes))
`], { input: html, encoding: 'utf8' });
  assert.equal(result.status, 0, result.stderr);
  return JSON.parse(result.stdout);
}

const hostile = '\"><img src=x onerror="sentinel"><span data-injected="';

test('workspace status shows and clears the metadata repair notice', () => {
  const ui = uiFixture();
  const notice = { hidden: true };
  ui.metadataRepairNotice = notice;
  ui.updateFromPayload({ metadata_needs_repair: true });
  assert.equal(notice.hidden, false);
  ui.updateFromPayload({});
  assert.equal(notice.hidden, false);
  ui.updateFromPayload({ metadata_needs_repair: false });
  assert.equal(notice.hidden, true);
});

test('a task receipt acknowledges one submission and opens its log on demand', async () => {
  const receipt = { task_run: { id: 42, status: 'requested', progress: 0, log: '' } };
  const snapshot = { ...receipt, workspace: 'synthetic' };
  for (const payload of [receipt, snapshot]) {
    const requests = [];
    const TaskUI = vm.runInNewContext(source + '\nInspectorUI;', {
      document: {
        addEventListener() {},
        createElement() {
          return { addEventListener(name, handler) { this[name] = handler; } };
        },
      },
      FormData: class {}, TextDecoder, AbortController,
      window: { setInterval() { return 1; }, setTimeout() { return 2; }, clearTimeout() {} },
      async fetch(url, options) {
        requests.push([url, options.method ?? 'GET']);
        if (options.method === 'POST') return new Response(JSON.stringify(payload), { status: 201 });
        if (url === '/status') return new Response('{"workspace":"synthetic","tasks":[]}');
        return new Response('{"status":"succeeded","log":"Synthetic task log"}');
      },
    });
    const ui = Object.create(TaskUI.prototype);
    ui.statusUrl = '/status';
    const updates = [];
    const toasts = [];
    const acknowledgments = [];
    let submit;
    let resets = 0;
    const form = {
      addEventListener(event, handler) { submit = handler; },
      reset() { resets++; },
    };
    const receiptRegion = {
      replaceChildren(...children) { acknowledgments.push(children); },
    };
    ui.root = {
      querySelector(selector) {
        return { '[data-task-form]': form, '[data-task-receipt]': receiptRegion }[selector];
      },
    };
    ui.taskEndpoint = '/api/task-runs/';
    ui.taskDetailTemplate = '/api/task-runs/0/';
    const drawerClasses = new Set();
    ui.taskDrawer = { classList: drawerClasses };
    ui.taskDrawerLog = { textContent: '' };
    ui.getCsrfToken = () => '';
    ui.updateFromPayload = (data) => updates.push(data);
    ui.showToast = (message) => toasts.push(message);
    ui.bindTaskForm();
    await ui.startPolling();
    submit({ preventDefault() {} });
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(resets, 1);
    assert.deepEqual(toasts, []);
    assert.deepEqual(updates.map((data) => data.tasks.length), [0, 0]);
    assert.deepEqual(requests, [['/status', 'GET'], ['/api/task-runs/', 'POST'], ['/status', 'GET']]);
    assert.equal(drawerClasses.has('is-visible'), false);
    assert.equal(acknowledgments.length, 2);
    assert.deepEqual(acknowledgments[0], []);
    const [message, button] = acknowledgments[1];
    assert.match(message, /Task #42 submitted/);
    assert.equal(button.type, 'button');
    assert.equal(button.textContent, 'View task log');
    button.click();
    await new Promise((resolve) => setImmediate(resolve));
    assert.deepEqual(requests.at(-1), ['/api/task-runs/42/', 'GET']);
    assert.equal(requests.length, 4);
    assert.equal(drawerClasses.has('is-visible'), true);
    assert.equal(ui.taskDrawerLog.textContent, 'Synthetic task log');
  }
});

test('each task submission clears the previous receipt, ignores resubmits while pending and times out', async () => {
  const replies = [];
  const posts = [];
  const signals = [];
  const timers = new Map();
  const TaskUI = vm.runInNewContext(source + '\nInspectorUI;', {
    document: {
      addEventListener() {},
      createElement() {
        return { addEventListener(name, handler) { this[name] = handler; } };
      },
    },
    FormData: class {}, TextDecoder, AbortController,
    window: {
      setTimeout(callback, delay) { const id = {}; timers.set(id, { callback, delay }); return id; },
      clearTimeout(id) { timers.delete(id); },
    },
    fetch(url, options) {
      posts.push([url, options.method]);
      signals.push(options.signal);
      return new Promise((resolve) => replies.push(resolve));
    },
  });
  const ui = Object.create(TaskUI.prototype);
  let submit;
  const submitButton = { disabled: false };
  const form = {
    addEventListener(event, handler) { submit = handler; },
    querySelector(selector) { return selector === '[type="submit"]' ? submitButton : null; },
    reset() {},
  };
  let receiptChildren = [];
  const receiptRegion = { replaceChildren(...children) { receiptChildren = children; } };
  ui.root = {
    querySelector(selector) {
      return { '[data-task-form]': form, '[data-task-receipt]': receiptRegion }[selector];
    },
  };
  ui.taskEndpoint = '/api/task-runs/';
  const toasts = [];
  ui.getCsrfToken = () => '';
  ui.updateFromPayload = () => {};
  ui.showToast = (message) => toasts.push(message);
  ui.bindTaskForm();
  const settle = () => new Promise((resolve) => setImmediate(resolve));

  submit({ preventDefault() {} });
  submit({ preventDefault() {} });
  assert.equal(posts.length, 1);
  assert.equal(submitButton.disabled, true);
  replies.shift()(new Response('{"task_run":{"id":41}}', { status: 201 }));
  await settle();
  assert.equal(submitButton.disabled, false);
  assert.match(receiptChildren[0], /Task #41 submitted/);

  submit({ preventDefault() {} });
  assert.deepEqual(receiptChildren, []);
  replies.shift()(new Response('{"broken":', { status: 201 }));
  await settle();
  assert.equal(posts.length, 2);
  assert.deepEqual(receiptChildren, []);
  assert.equal(submitButton.disabled, false);
  assert.match(toasts.at(-1), /check task history before retrying/i);
  assert.equal(timers.size, 0);

  // A POST that never answers releases the form after one deadline.
  submit({ preventDefault() {} });
  assert.equal(submitButton.disabled, true);
  assert.equal(timers.size, 1);
  const [timer] = timers.values();
  assert.equal(timer.delay, 30000);
  timer.callback();
  await settle();
  assert.equal(signals.at(-1).aborted, true);
  assert.equal(submitButton.disabled, false);
  assert.deepEqual(receiptChildren, []);
  assert.equal(toasts.length, 2);
  assert.match(toasts.at(-1), /check task history before retrying/i);
  submit({ preventDefault() {} });
  assert.equal(posts.length, 4);
});

test('metadata cannot create renderer elements or attributes', () => {
  const ui = uiFixture();
  ui.updateScans([{ status: hostile, kind: hostile, kind_label: hostile,
    progress: hostile, summary: hostile }]);
  ui.updateInsights([{ severity: hostile, title: hostile, value: hostile, caption: hostile }]);
  ui.updateActivity([{ status: hostile, message: hostile, kind: hostile }]);
  ui.updateLogs([{ level: hostile, message: hostile }]);
  ui.updateTasks([{ status: hostile, id: hostile, progress: hostile, label: hostile }]);
  ui.updateTemplateOptions([{ slug: hostile, title: hostile }]);
  ui.renderDataLabList([{ slug: hostile, viewer_url: hostile, title: hostile }]);
  for (const key of ['scanBoard', 'insightGrid', 'activityFeed', 'logStream', 'taskHistory',
    'select', 'dataLabList']) {
    const nodes = parse(ui[key].innerHTML);
    assert.ok(nodes.length > 0, key);
    for (const [tag, attrs] of nodes) {
      assert.notEqual(tag, 'img', key);
      assert.ok(!('data-injected' in attrs), key);
      assert.ok(!Object.keys(attrs).some((name) => name.startsWith('on')), key);
    }
  }
  assert.match(ui.scanBoard.innerHTML, /scan-card--pending/);
  assert.match(ui.insightGrid.innerHTML, /insight-card--info/);
  assert.match(ui.activityFeed.innerHTML, /activity-status--info/);
  assert.match(ui.logStream.innerHTML, /log-level--info/);
  assert.match(ui.taskHistory.innerHTML, /task-history-item--requested/);
  assert.match(ui.insightGrid.innerHTML, /&lt;img/);
});

test('supported classes, numeric progress and labels survive rendering', () => {
  const ui = uiFixture();
  ui.updateScans([{ status: 'completed', kind: 'schema', kind_label: 'Schema', progress: 100 }]);
  ui.updateInsights([{ severity: 'warning', title: 'Migrations', value: 3 }]);
  ui.updateActivity([{ status: 'success', kind: 'scan', message: 'Done' }]);
  ui.updateLogs([{ level: 'warning', message: 'Review' }]);
  ui.updateTasks([{ status: 'succeeded', id: 42, progress: 100, label: 'Check' }]);
  assert.match(ui.scanBoard.innerHTML, /scan-card--completed/);
  assert.match(ui.scanBoard.innerHTML, /100%/);
  assert.match(ui.insightGrid.innerHTML, /insight-card--warning/);
  assert.match(ui.activityFeed.innerHTML, /activity-status--success/);
  assert.match(ui.logStream.innerHTML, /log-level--warning/);
  assert.match(ui.logStream.innerHTML, /WARNING/);
  assert.match(ui.taskHistory.innerHTML, /task-history-item--succeeded/);
  assert.match(ui.taskHistory.innerHTML, /data-task-run="42"/);
  assert.match(ui.taskHistory.innerHTML, /width:100%/);
});
