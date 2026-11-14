// Real rendered drawer DOM, synthetic data, no server or external network.
const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright-core');

const root = path.resolve(__dirname, '../..');
const rendered = spawnSync(process.env.PYTHON || path.join(root, '.venv/bin/python'), ['-c', `
import django
django.setup()
from django.core.management import call_command
from django.test import Client, override_settings
from tempfile import TemporaryDirectory
from djdesk.inspector.models import Workspace
from djdesk.inspector import data_lab
call_command('migrate', verbosity=0)
with TemporaryDirectory() as directory, override_settings(
    INSPECTOR_DATA_LAB_ROOT=directory, ALLOWED_HOSTS=['testserver']
):
    workspace = Workspace.objects.create(name='Synthetic drawer', project_path='/tmp/synthetic-drawer')
    data_lab.export_notebook(workspace, 'schema-audit')
    data_lab.export_notebook(workspace, 'log-study')
    print(Client().get('/', {'workspace': workspace.slug}).content.decode())
`], { cwd: root, env: { ...process.env, DJANGO_SETTINGS_MODULE: 'djdesk.settings.test' },
  encoding: 'utf8' });
assert.equal(rendered.status, 0, rendered.stderr);
const html = rendered.stdout.replace(/<script\b[^>]*\bsrc=[^>]*><\/script>/g, '');
const source = fs.readFileSync(path.join(root, 'src/djdesk/inspector/static/inspector/app.js'), 'utf8');

async function main() {
  const browser = await chromium.launch({
    ...(process.env.CHROME_EXECUTABLE ? { executablePath: process.env.CHROME_EXECUTABLE } : {}),
  });
  try {
    const page = await browser.newPage();
    // Serve the document from a synthetic origin: about:blank denies document.cookie,
    // which the CSRF lookup reads. Every other request is aborted.
    const origin = 'http://djdesk-drawer.invalid/';
    await page.route('**/*', (route) => (route.request().url() === origin
      ? route.fulfill({ status: 200, contentType: 'text/html', body: html })
      : route.abort()));
    await page.goto(origin);
    await page.addScriptTag({ content: source + '\nwindow.InspectorUI = InspectorUI;' });
    await page.evaluate(() => {
      window.requests = [];
      window.fetch = () => new Promise((resolve, reject) => requests.push({ resolve, reject }));
      window.ui = new InspectorUI(document.querySelector('[data-inspector-shell]'));
      ui.bindDataLab();
      const form = document.querySelector('[data-data-lab-form]');
      form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
      form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    });
    assert.equal(await page.evaluate(() => requests.length), 1, 'one owner must issue only one POST');
    assert.equal(await page.locator('[data-data-lab-form] button').isDisabled(), true);
    await page.locator('[data-notebook-slug="log-study"]').click();
    await page.evaluate(() => requests[0].resolve({ ok: true, json: async () => ({
      data_lab: { selected_notebook: 'schema-audit-2', notebooks: [
        { slug: 'schema-audit-2', viewer_url: '/synthetic-new', title: 'New' },
      ] },
    }) }));
    await page.waitForFunction(() => !document.querySelector('[data-data-lab-form] button').disabled);
    assert.equal(await page.evaluate(() => ui.activeNotebookSlug), 'log-study', 'manual choice owns selection');
    assert.match(await page.locator('[data-data-lab-frame]').getAttribute('src'), /log-study/);

    // Network, HTTP and JSON failures each release controls and permit deliberate retry.
    for (const failure of ['network', 'http', 'json']) {
      await page.locator('[data-data-lab-form] button').click();
      await page.evaluate((kind) => {
        const pending = requests.at(-1);
        if (kind === 'network') pending.reject(new Error('Synthetic network failure'));
        else if (kind === 'http') pending.resolve({ ok: false,
          json: async () => ({ errors: { template: ['Synthetic refusal'] } }) });
        else pending.resolve({ ok: true, json: async () => { throw new Error('Synthetic JSON failure'); } });
      }, failure);
      await page.waitForFunction(() => !document.querySelector('[data-data-lab-form] button').disabled);
    }
    assert.equal(await page.evaluate(() => requests.length), 4, 'no automatic POST retries');
    await page.locator('[data-data-lab-form] button').click();
    await page.evaluate(() => requests.at(-1).resolve({ ok: true, json: async () => ({
      data_lab: { selected_notebook: 'schema-audit-9', selected_notebook_url: '/synthetic-new-9',
        notebooks: [], notebooks_incomplete: true },
    }) }));
    await page.waitForFunction(() => ui.activeNotebookSlug === 'schema-audit-9');
    await page.evaluate(() => ui.updateDataLab({ notebooks: [], notebooks_incomplete: true }));
    assert.equal(await page.evaluate(() => ui.activeNotebookSlug), 'schema-audit-9', 'limited polls retain the current viewer');
    assert.equal(await page.locator('[data-data-lab-incomplete]').isVisible(), true);
    console.log('Notebook drawer DOM passed: owned POST, manual choice, retry, truncated inventory');

    await page.evaluate(() => {
      window.cancelledResponses = 0;
      window.oversizedJSONParses = 0;
      window.fetch = async () => {
        const response = new Response(new ReadableStream({
          start(controller) {
            controller.enqueue(new Uint8Array(4 * 1024 * 1024));
            controller.enqueue(new Uint8Array(4 * 1024 * 1024 + 1));
          },
          cancel() { cancelledResponses++; },
        }), { headers: { 'Content-Length': '1' } });
        response.json = async () => { oversizedJSONParses++; return {}; };
        return response;
      };
      ui.updateLogs([{ level: 'info', message: 'Previous valid status' }]);
      ui.taskDrawer.classList.add('is-visible');
      ui.renderTaskDrawer({ label: 'Previous valid task', status: 'succeeded', log: 'Previous valid log' });
      ui.startPolling();
      clearInterval(ui.polling);
      ui.loadTaskDetails(123);
    });
    await page.waitForFunction(() => cancelledResponses === 2);
    assert.match(await page.locator('[data-log-stream]').textContent(), /Previous valid status/);
    assert.equal(await page.locator('[data-task-drawer-log]').textContent(), 'Previous valid log');
    assert.equal(await page.locator('[data-task-drawer]').evaluate((node) => node.classList.contains('is-visible')), true);
    assert.equal(await page.evaluate(() => oversizedJSONParses), 0);
    await page.evaluate(() => {
      window.fetch = async () => new Response(JSON.stringify({
        label: 'Recovered task', status: 'succeeded', log: 'Small response works',
      }));
      ui.loadTaskDetails(123);
    });
    await page.waitForFunction(() => document.querySelector('[data-task-drawer-log]').textContent === 'Small response works');
    const unicode = await page.evaluate(async () => {
      const bytes = new TextEncoder().encode('{"text":"é€"}');
      const body = new ReadableStream({ start(controller) {
        for (const byte of bytes) controller.enqueue(Uint8Array.of(byte));
        controller.close();
      } });
      return (await readInspectorJSON(new Response(body), bytes.length)).text;
    });
    assert.equal(unicode, 'é€');
    console.log('Inspector response DOM passed: bounded streams, preserved status/drawer, retry, Unicode boundary');
  } finally {
    await browser.close();
  }
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
