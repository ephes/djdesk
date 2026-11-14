// Real app.js and the production Cytoscape version, synthetic schema, no network.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright-core');

const source = fs.readFileSync(path.join(__dirname,
  '../../src/djdesk/inspector/static/inspector/app.js'), 'utf8');

function schemaOracle() {
  const check = (condition, message) => { if (!condition) throw new Error(message); };
  const original = window.cytoscape;
  const graphs = [];
  let failure = null;
  let layouts = 0;
  window.cytoscape = (options) => {
    if (failure === 'constructor') throw new Error('Synthetic constructor failure');
    const graph = original(options);
    graphs.push(graph);
    const layout = graph.layout.bind(graph);
    graph.layout = (settings) => {
      if (settings.name === 'cose') {
        layouts++;
        if (failure === 'layout') return { run() { throw new Error('Synthetic layout failure'); } };
      }
      return layout(settings);
    };
    const ready = graph.ready.bind(graph);
    graph.savedReady = [];
    graph.ready = (callback) => { graph.savedReady.push(callback); return ready(callback); };
    return graph;
  };
  const ui = new InspectorUI(document.querySelector('[data-inspector-shell]'));
  for (const method of ['toggleConnection', 'updateScans', 'updateInsights', 'updateActivity',
    'updateLogs', 'updateTasks', 'updateDataLab', 'refreshIcons']) ui[method] = () => {};
  const schema = { nodes: [{ name: 'Book', fields: ['id'] }, { name: 'Author' }],
    connections: [{ source: 'Book', target: 'Author' }] };
  localStorage.setItem('djdesk.schema.synthetic', JSON.stringify({ zoom: 1.2, pan: { x: 3, y: 4 } }));
  ui.updateFromPayload({ schema });
  const initial = ui.schemaGraphInstance;
  initial.zoom(1.6);
  initial.pan({ x: 61, y: -13 });
  for (let i = 0; i < 20; i++) ui.updateFromPayload({ schema: structuredClone(schema) });
  check(graphs.length === 1 && layouts === 1, 'identical polls must keep one graph/layout');
  check(ui.schemaGraphInstance === initial && initial.pan().x === 61, 'retain live graph and viewport');
  const changed = structuredClone(schema);
  changed.nodes[0].fields.push('title');
  changed.nodes.push({ name: 'Publisher' });
  changed.connections.push({ source: 'Book', target: 'Publisher' });
  for (const kind of ['constructor', 'layout']) {
    failure = kind;
    let rejected = false;
    try { ui.updateSchema(changed); } catch { rejected = true; }
    check(rejected, 'exercise the injected failure');
    check(ui.schemaGraphInstance === initial && !initial.destroyed(), 'failure must keep the old graph');
    check(initial.nodes().length === 2, 'failure must preserve the old data');
    check(ui.schemaCanvas.children.length === 2, 'remove failed staging containers');
    check(graphs.slice(1).every((graph) => graph.destroyed()), 'dispose failed candidates');
  }
  failure = null;
  ui.updateSchema(changed);
  const current = ui.schemaGraphInstance;
  check(current !== initial && initial.destroyed(), 'install one successful replacement');
  check(current.nodes().length === 3 && current.edges().length === 2, 'apply schema delta');
  check(current.getElementById('Book').data('fields').includes('title'), 'apply changed metadata');
  check(current.zoom() === 1.6 && current.pan().x === 61, 'retain the current viewport after layout');
  current.zoom(1.3);
  initial.savedReady.forEach((callback) => callback());
  check(current.zoom() === 1.3, 'retired ready callbacks cannot move the new graph');
  const before = layouts;
  ui.updateSchema(structuredClone(changed));
  check(layouts === before, 'new schema polls must also settle');
  // Removing a node also removes its incident edges through the public API.
  ui.updateSchema({ nodes: [{ name: 'Book', fields: ['id'] }], connections: [] });
  check(ui.schemaGraphInstance.nodes().length === 1 && ui.schemaGraphInstance.edges().length === 0,
    'remove obsolete nodes and edges');
  ui.updateSchema({ nodes: [] });
  check(ui.schemaGraphInstance === null, 'empty schema retires the view');
  ui.updateSchema(schema);
  check(ui.schemaGraphInstance.nodes().length === 2, 'recover after an empty schema');
  ui.schemaGraphInstance.destroy();
  return { polls: 20, failures: 2, passed: true };
}

async function main() {
  if (process.argv.includes('--headless')) {
    runHeadless();
    return;
  }
  const browser = await chromium.launch({
    ...(process.env.CHROME_EXECUTABLE ? { executablePath: process.env.CHROME_EXECUTABLE } : {}),
  });
  try {
    const page = await browser.newPage();
    const origin = 'http://djdesk-schema.invalid/';
    await page.route('**/*', (route) => (route.request().url() === origin
      ? route.fulfill({ contentType: 'text/html', body: `
        <div data-inspector-shell data-workspace-slug="synthetic">
          <div id="schema-canvas" style="position:relative;width:800px;height:360px"></div>
        </div>` }) : route.abort()));
    await page.goto(origin);
    await page.addScriptTag({ content: fs.readFileSync(require.resolve('cytoscape/dist/cytoscape.min.js'), 'utf8') });
    await page.addScriptTag({ content: source + '\nwindow.InspectorUI = InspectorUI;' });
    const result = await page.evaluate(schemaOracle);
    assert.equal(result.passed, true);
    console.log('Schema browser passed:', result);
  } finally {
    await browser.close();
  }
}

function runHeadless() {
  const vm = require('node:vm');
  function element() {
    return {
      style: {}, children: [], classList: { add() {}, remove() {} },
      appendChild(child) { child.remove(); this.children.push(child); child.parentElement = this; },
      remove() {
        if (this.parentElement) {
          const parent = this.parentElement;
          parent.children.splice(parent.children.indexOf(this), 1);
          this.parentElement = null;
        }
      },
      set innerHTML(value) { this.children.forEach((child) => { child.parentElement = null; }); this.children = []; },
    };
  }
  const canvas = element();
  const root = { dataset: { workspaceSlug: 'synthetic' }, querySelectorAll: () => [],
    querySelector: (selector) => selector === '#schema-canvas' ? canvas : null };
  const storage = new Map();
  const context = vm.createContext({
    document: { addEventListener() {}, createElement: element, querySelector: () => root },
    window: { setTimeout: () => 1 },
    clearTimeout() {},
    localStorage: { getItem: (key) => storage.get(key) || null,
      setItem: (key, value) => storage.set(key, value) },
  });
  vm.runInContext(fs.readFileSync(require.resolve('cytoscape/dist/cytoscape.min.js'), 'utf8'), context);
  vm.runInContext(`
    window.cytoscape = (options) => cytoscape({ ...options, container: undefined, headless: true });
    globalThis.structuredClone = (value) => JSON.parse(JSON.stringify(value));
  `, context);
  const result = vm.runInContext(source + '\n' + schemaOracle.toString() + '\nschemaOracle();', context);
  assert.equal(result.passed, true);
  console.log('Schema headless Cytoscape passed:', result);
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
