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

function clickDocLink({ bridge, external, modifier }) {
  const ui = Object.create(InspectorUI.prototype);
  const opened = [];
  const drawer = [];
  ui.nativeBridge = bridge ? { openExternal: (url) => opened.push(url) } : null;
  ui.openDocsDrawer = (url) => drawer.push(url);
  ui.navigateToPane = () => {};
  let listener;
  ui.docLinks = [{
    href: 'https://docs.invalid/guide/',
    dataset: { docUrl: 'https://docs.invalid/guide/', docExternal: String(external) },
    addEventListener: (_type, handler) => { listener = handler; },
  }];
  ui.bindDocLinks();
  const event = { metaKey: modifier, ctrlKey: false, prevented: false,
    preventDefault() { this.prevented = true; } };
  listener(event);
  return { opened, drawer, prevented: event.prevented };
}

test('modifier-clicked doc links use the validated bridge instead of a denied popup', () => {
  for (const external of [true, false]) {
    assert.deepEqual(clickDocLink({ bridge: true, external, modifier: true }),
      { opened: ['https://docs.invalid/guide/'], drawer: [], prevented: true });
  }
});

test('modifier clicks keep browser defaults without the native bridge', () => {
  assert.deepEqual(clickDocLink({ bridge: false, external: true, modifier: true }),
    { opened: [], drawer: [], prevented: false });
});

test('plain doc link clicks open the drawer and dispatch external links once', () => {
  assert.deepEqual(clickDocLink({ bridge: true, external: true, modifier: false }),
    { opened: ['https://docs.invalid/guide/'], drawer: ['https://docs.invalid/guide/'], prevented: true });
  assert.deepEqual(clickDocLink({ bridge: true, external: false, modifier: false }),
    { opened: [], drawer: ['https://docs.invalid/guide/'], prevented: true });
});
