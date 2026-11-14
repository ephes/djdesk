const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const source = readFileSync(path.join(__dirname,
  '../../src/djdesk/inspector/static/inspector/app.js'), 'utf8');
const copy = (value) => JSON.parse(JSON.stringify(value));
const schema = () => ({ nodes: [
  { name: 'Book', fields: ['id'], relations: [], pending_migrations: 0 },
  { name: 'Author', fields: ['id', 'name'], relations: [] },
], connections: [{ source: 'Book', target: 'Author' }] });

function fixture() {
  const graphs = [];
  const timers = [];
  const storage = new Map();
  function element() {
    return {
      style: {}, children: [], classList: { add() {}, remove() {} },
      appendChild(child) { child.remove?.(); this.children.push(child); child.parentElement = this; },
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
  const state = { layouts: 0, fail: null, writes: [] };
  const context = {
    document: { addEventListener() {}, createElement: element },
    localStorage: {
      getItem: (key) => storage.get(key) || null,
      setItem(key, value) { state.writes.push(JSON.parse(value)); storage.set(key, value); },
    },
    clearTimeout(id) { timers[id - 1] = null; },
    window: {
      setTimeout(fn) { timers.push(fn); return timers.length; },
      cytoscape(options) {
        if (state.fail === 'constructor') throw new Error('Synthetic constructor failure');
        const graph = {
          options, items: copy(options.elements), handlers: {}, readyCallbacks: [],
          destroyed: false, edits: 0, viewport: { zoom: 1, pan: { x: 0, y: 0 } },
          destroy() { this.destroyed = true; },
          zoom(value) { if (value !== undefined) this.viewport.zoom = value; return this.viewport.zoom; },
          pan(value) { if (value !== undefined) this.viewport.pan = copy(value); return this.viewport.pan; },
          ready(fn) { this.readyCallbacks.push(fn); },
          on(name, selector, fn) { this.handlers[name] = fn || selector; },
          off(name) { delete this.handlers[name]; },
          batch(fn) { fn(); },
          elements() { return { jsons: () => copy(this.items),
            forEach: (fn) => this.items.slice().forEach((item) => fn(this.getElementById(item.data.id))) }; },
          getElementById(id) {
            const item = this.items.find((entry) => entry.data.id === id);
            return { length: item ? 1 : 0, id: () => id,
              data: (data) => { if (data) { item.data = copy(data); this.edits++; } return item.data; },
              remove: () => { this.items = this.items.filter((entry) => entry !== item); this.edits++; } };
          },
          add(item) { this.items.push(copy(item)); this.edits++; },
          layout() { return { run() {
            state.layouts++;
            if (state.fail === 'layout') throw new Error('Synthetic layout failure');
          } }; },
        };
        graphs.push(graph);
        if (options.layout.name !== 'preset') graph.layout().run();
        return graph;
      },
    },
  };
  const InspectorUI = vm.runInNewContext(source + '\nInspectorUI;', context);
  const canvas = element();
  const root = { dataset: { workspaceSlug: 'synthetic' },
    querySelector: (selector) => selector === '#schema-canvas' ? canvas : null,
    querySelectorAll: () => [] };
  const ui = new InspectorUI(root);
  for (const method of ['toggleConnection', 'updateScans', 'updateInsights', 'updateActivity',
    'updateLogs', 'updateTasks', 'updateDataLab', 'refreshIcons']) ui[method] = () => {};
  return { ui, graphs, state, storage, timers, canvas };
}

test('identical status polls retain the live graph, layout and viewport', () => {
  const { ui, graphs, state } = fixture();
  ui.updateFromPayload({ schema: schema() });
  const graph = ui.schemaGraphInstance;
  graph.zoom(1.7);
  graph.pan({ x: 73, y: -19 });
  for (let i = 0; i < 20; i++) ui.updateFromPayload({ schema: copy(schema()), tasks: [{ id: i }] });
  // Object property order and unrelated schema metadata do not change rendered data.
  const reordered = schema();
  reordered.nodes = reordered.nodes.reverse().map((node) => Object.fromEntries(Object.entries(node).reverse()));
  reordered.generated_at = 'synthetic';
  ui.updateSchema(reordered);
  assert.equal(ui.schemaGraphInstance, graph);
  assert.equal(graphs.length, 1);
  assert.equal(state.layouts, 1);
  assert.equal(graph.destroyed, false);
  assert.equal(graph.zoom(), 1.7);
  assert.deepEqual(graph.pan(), { x: 73, y: -19 });
});

test('a schema change applies once through graph APIs and preserves the live viewport', () => {
  const { ui, state } = fixture();
  ui.updateSchema(schema());
  ui.schemaGraphInstance.zoom(1.4);
  ui.schemaGraphInstance.pan({ x: 42, y: 13 });
  ui.workspaceSlug = ''; // A live viewport also survives without saved workspace state.
  const changed = schema();
  changed.nodes[0].fields.push('title');
  changed.nodes.push({ name: 'Publisher' });
  changed.connections.push({ source: 'Author', target: 'Publisher' });
  ui.updateSchema(changed);
  const graph = ui.schemaGraphInstance;
  assert.equal(graph.items.length, 5);
  assert.equal(graph.edits, 3, 'only one metadata change, one new node and one new edge');
  assert.equal(graph.zoom(), 1.4);
  assert.deepEqual(graph.pan(), { x: 42, y: 13 });
  const layouts = state.layouts;
  ui.updateSchema(copy(changed));
  const reordered = copy(changed);
  reordered.nodes.reverse();
  reordered.connections.reverse();
  ui.updateSchema(reordered);
  assert.equal(state.layouts, layouts);
  assert.equal(ui.schemaGraphInstance, graph);
});

for (const failure of ['constructor', 'layout']) {
  test(`${failure} failure keeps the previous graph and permits a later retry`, () => {
    const { ui, graphs, state, canvas } = fixture();
    ui.updateSchema(schema());
    const previous = ui.schemaGraphInstance;
    const before = copy(previous.items);
    const children = [...canvas.children];
    previous.zoom(1.8);
    previous.pan({ x: 23, y: -17 });
    const changed = schema();
    changed.nodes.push({ name: 'Publisher' });
    state.fail = failure;
    try { ui.updateSchema(changed); } catch {} // Polling also handles a rejected update.
    assert.equal(ui.schemaGraphInstance, previous);
    assert.equal(previous.destroyed, false);
    assert.deepEqual(previous.items, before, 'old graph data must remain usable');
    assert.deepEqual(canvas.children, children, 'failed staging must leave no canvas or tooltip debris');
    assert.ok(graphs.slice(1).every((graph) => graph.destroyed), 'dispose constructed candidates');
    state.fail = null;
    ui.updateSchema(changed);
    assert.notEqual(ui.schemaGraphInstance, previous, 'the failed signature must be retried');
    assert.equal(previous.destroyed, true);
    assert.equal(ui.schemaGraphInstance.zoom(), 1.8);
    assert.deepEqual(ui.schemaGraphInstance.pan(), { x: 23, y: -17 });
  });
}

test('retired ready, tooltip and persistence callbacks cannot affect a new generation', () => {
  const { ui, storage, state, timers } = fixture();
  storage.set('djdesk.schema.synthetic', JSON.stringify({ zoom: 2, pan: { x: 9, y: 12 } }));
  ui.updateSchema(schema());
  const previous = ui.schemaGraphInstance;
  const oldReady = previous.readyCallbacks[0];
  const oldPersist = previous.handlers['pan zoom'];
  const oldHover = previous.handlers.mouseover;
  oldPersist();
  const queued = timers.at(-1);
  const changed = schema();
  changed.nodes.push({ name: 'Publisher' });
  ui.updateSchema(changed);
  const current = ui.schemaGraphInstance;
  current.zoom(1.3);
  current.pan({ x: 41, y: 19 });
  oldReady();
  oldPersist();
  queued();
  oldHover({ target: { id: () => 'Book' } });
  assert.equal(current.zoom(), 1.3);
  assert.deepEqual(current.pan(), { x: 41, y: 19 });
  assert.equal(state.writes.length, 0, 'retired callbacks must not persist the new viewport');
});

test('empty schemas retire callbacks, remove the graph and allow fresh reconstruction', () => {
  const { ui, state } = fixture();
  ui.updateSchema(schema());
  const graph = ui.schemaGraphInstance;
  const persist = graph.handlers['pan zoom'];
  ui.updateSchema({ nodes: [] });
  persist();
  assert.equal(graph.destroyed, true);
  assert.equal(ui.schemaGraphInstance, null);
  assert.equal(state.writes.length, 0);
  ui.updateSchema(schema());
  assert.notEqual(ui.schemaGraphInstance, graph);
});
