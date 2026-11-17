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

function clickDocLink({ bridge, external, modifier, type = 'click', button = 0, shift = false }) {
  const ui = Object.create(InspectorUI.prototype);
  const opened = [];
  const drawer = [];
  ui.nativeBridge = bridge ? { openExternal: (url) => opened.push(url) } : null;
  ui.openDocsDrawer = (url) => drawer.push(url);
  ui.navigateToPane = () => {};
  const listeners = {};
  ui.docLinks = [{
    href: 'https://docs.invalid/guide/',
    dataset: { docUrl: 'https://docs.invalid/guide/', docExternal: String(external) },
    addEventListener: (name, handler) => { listeners[name] = handler; },
  }];
  ui.bindDocLinks();
  const event = { type, button, metaKey: modifier, ctrlKey: false, shiftKey: shift, prevented: false,
    preventDefault() { this.prevented = true; } };
  listeners[type](event);
  return { opened, drawer, prevented: event.prevented };
}

test('modifier-clicked external doc links use the validated bridge instead of a denied popup', () => {
  assert.deepEqual(clickDocLink({ bridge: true, external: true, modifier: true }),
    { opened: ['https://docs.invalid/guide/'], drawer: [], prevented: true });
});

test('modifier-clicked local doc links stay in the drawer inside the shell', () => {
  assert.deepEqual(clickDocLink({ bridge: true, external: false, modifier: true }),
    { opened: [], drawer: ['https://docs.invalid/guide/'], prevented: true });
});

test('modifier clicks keep browser defaults without the native bridge', () => {
  for (const external of [true, false]) {
    assert.deepEqual(clickDocLink({ bridge: false, external, modifier: true }),
      { opened: [], drawer: [], prevented: false });
  }
});

test('plain doc link clicks open the drawer and dispatch external links once', () => {
  assert.deepEqual(clickDocLink({ bridge: true, external: true, modifier: false }),
    { opened: ['https://docs.invalid/guide/'], drawer: ['https://docs.invalid/guide/'], prevented: true });
  assert.deepEqual(clickDocLink({ bridge: true, external: false, modifier: false }),
    { opened: [], drawer: ['https://docs.invalid/guide/'], prevented: true });
});

test('middle-click and shift-click follow the modifier-click routing', () => {
  for (const options of [{ type: 'auxclick', button: 1 }, { shift: true }]) {
    assert.deepEqual(clickDocLink({ bridge: true, external: true, modifier: false, ...options }),
      { opened: ['https://docs.invalid/guide/'], drawer: [], prevented: true });
    assert.deepEqual(clickDocLink({ bridge: true, external: false, modifier: false, ...options }),
      { opened: [], drawer: ['https://docs.invalid/guide/'], prevented: true });
    assert.deepEqual(clickDocLink({ bridge: false, external: true, modifier: false, ...options }),
      { opened: [], drawer: [], prevented: false });
  }
});

test('other auxiliary buttons are ignored', () => {
  assert.deepEqual(clickDocLink({ bridge: true, external: true, modifier: false, type: 'auxclick', button: 2 }),
    { opened: [], drawer: [], prevented: false });
});
