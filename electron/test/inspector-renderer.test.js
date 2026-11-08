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
