// Real rendered drawer DOM, synthetic data, no server or external network.
const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright-core');

const root = path.resolve(__dirname, '../..');
const rendered = spawnSync(process.env.PYTHON || path.join(root, '.venv/bin/python'), ['-c', `
import django, json
django.setup()
from django.core.management import call_command
from django.test import Client, override_settings
from django.urls import reverse
from tempfile import TemporaryDirectory
from unittest.mock import patch
from djdesk.inspector.models import Workspace
from djdesk.inspector import data_lab
call_command('migrate', verbosity=0)
with TemporaryDirectory() as directory, override_settings(
    INSPECTOR_DATA_LAB_ROOT=directory, ALLOWED_HOSTS=['testserver']
):
    workspace = Workspace.objects.create(name='Synthetic drawer', project_path='/tmp/synthetic-drawer')
    data_lab.export_notebook(workspace, 'schema-audit')
    data_lab.export_notebook(workspace, 'log-study')
    client = Client()
    html = client.get('/', {'workspace': workspace.slug}).content.decode()
    with patch('djdesk.inspector.views.workspace_status_payload', side_effect=OSError('Synthetic inventory error')):
        response = client.post(reverse('inspector:data-lab-export', args=[workspace.slug]), {'template': 'schema-audit'})
    assert response.status_code == 201, response.content
    receipt = response.json()
    viewer = client.get(receipt['data_lab']['selected_notebook_url'])
    assert viewer.status_code == 200
    print(json.dumps({'html': html, 'receipt': receipt, 'viewer': viewer.content.decode()}))
`], { cwd: root, env: { ...process.env, DJANGO_SETTINGS_MODULE: 'djdesk.settings.test' },
  encoding: 'utf8' });
assert.equal(rendered.status, 0, rendered.stderr);
const fixture = JSON.parse(rendered.stdout);
const html = fixture.html.replace(/<script\b[^>]*\bsrc=[^>]*><\/script>/g, '');
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
    const viewerURL = new URL(fixture.receipt.data_lab.selected_notebook_url, origin).href;
    await page.route('**/*', (route) => {
      const url = route.request().url();
      if (url === origin || url === viewerURL) {
        return route.fulfill({ status: 200, contentType: 'text/html',
          body: url === origin ? html : fixture.viewer });
      }
      return route.abort();
    });
    await page.goto(origin);
    await page.addScriptTag({ content: source + '\nwindow.InspectorUI = InspectorUI;' });
    await page.evaluate(() => {
      window.requests = [];
      window.fetch = (url, options) => new Promise((resolve, reject) => requests.push({ resolve, reject, options }));
      window.ui = new InspectorUI(document.querySelector('[data-inspector-shell]'));
      ui.bindDataLab();
      const form = document.querySelector('[data-data-lab-form]');
      form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
      form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    });
    assert.equal(await page.evaluate(() => requests.length), 1, 'one owner must issue only one POST');
    assert.equal(await page.locator('[data-data-lab-form] button').isDisabled(), true);
    await page.locator('[data-notebook-slug="log-study"]').click();
    await page.evaluate(() => requests[0].resolve(new Response(JSON.stringify({
      data_lab: { selected_notebook: 'schema-audit-2', notebooks: [
        { slug: 'schema-audit-2', viewer_url: '/synthetic-new', title: 'New' },
      ] },
    }))));
    await page.waitForFunction(() => !document.querySelector('[data-data-lab-form] button').disabled);
    assert.equal(await page.evaluate(() => ui.activeNotebookSlug), 'log-study', 'manual choice owns selection');
    assert.match(await page.locator('[data-data-lab-frame]').getAttribute('src'), /log-study/);

    // Network, HTTP and JSON failures each release controls and permit deliberate retry.
    for (const failure of ['network', 'http', 'json']) {
      await page.locator('[data-data-lab-form] button').click();
      await page.evaluate((kind) => {
        const pending = requests.at(-1);
        if (kind === 'network') pending.reject(new Error('Synthetic network failure'));
        else if (kind === 'http') pending.resolve(new Response(JSON.stringify({
          errors: { template: ['Synthetic refusal'] },
        }), { status: 400 }));
        else pending.resolve(new Response('invalid JSON'));
      }, failure);
      await page.waitForFunction(() => !document.querySelector('[data-data-lab-form] button').disabled);
    }
    assert.equal(await page.evaluate(() => requests.length), 4, 'no automatic POST retries');
    await page.locator('[data-data-lab-form] button').click();
    await page.evaluate(() => requests.at(-1).resolve(new Response(JSON.stringify({
      data_lab: { selected_notebook: 'schema-audit-9', selected_notebook_url: '/synthetic-new-9',
        notebooks: [], notebooks_incomplete: true },
    }))));
    await page.waitForFunction(() => ui.activeNotebookSlug === 'schema-audit-9');
    await page.evaluate(() => ui.updateDataLab({ notebooks: [], notebooks_incomplete: true }));
    assert.equal(await page.evaluate(() => ui.activeNotebookSlug), 'schema-audit-9', 'limited polls retain the current viewer');
    assert.equal(await page.locator('[data-data-lab-incomplete]').isVisible(), true);
    console.log('Notebook drawer DOM passed: owned POST, manual choice, retry, truncated inventory');

    // Feed the real API receipt produced after publication and a failed inventory read.
    const inventory = await page.evaluate(() => ({
      list: ui.dataLabList.innerHTML,
      templates: ui.dataLabForm.querySelector('select').innerHTML,
    }));
    await page.locator('[data-data-lab-form] button').click();
    await page.evaluate((receipt) => requests.at(-1).resolve(new Response(JSON.stringify(receipt))), fixture.receipt);
    await page.waitForFunction(() => !document.querySelector('[data-data-lab-form] button').disabled);
    assert.equal(await page.evaluate(() => ui.activeNotebookSlug), fixture.receipt.data_lab.selected_notebook);
    assert.equal(await page.evaluate(() => requests.length), 6, 'receipt never replays export');
    assert.equal(await page.evaluate(() => ui.dataLabList.innerHTML), inventory.list);
    assert.equal(await page.evaluate(() => ui.dataLabForm.querySelector('select').innerHTML), inventory.templates);
    assert.equal(await page.locator('[data-data-lab-incomplete]').isVisible(), true);
    // The viewer heading is the template title; the published slug is in the frame URL.
    assert.equal(new URL(await page.locator('[data-data-lab-frame]').getAttribute('src'), origin).pathname,
      fixture.receipt.data_lab.selected_notebook_url);
    assert.match(fixture.receipt.data_lab.selected_notebook_url, /schema-audit-2/);
    assert.equal(await page.frameLocator('[data-data-lab-frame]').locator('h1').textContent(), 'Schema audit starter');
    console.log('Committed export receipt DOM passed: published viewer, retained inventory/templates, one POST');

    // The rendered dashboard carries the server-derived ceiling (default settings).
    const ceiling = await page.evaluate(() => ui.responseBytes);
    assert.equal(ceiling, 8 * 1024 * 1024 + 5 * 6 * 1024 * 1024, 'dashboard response ceiling');
    await page.evaluate(async (limit) => {
      window.cancelledResponses = 0;
      window.oversizedJSONParses = 0;
      window.fetch = async () => {
        const response = new Response(new ReadableStream({
          start(controller) {
            controller.enqueue(new Uint8Array(limit));
            controller.enqueue(new Uint8Array(1));
          },
          cancel() { cancelledResponses++; },
        }), { headers: { 'Content-Length': '1' } });
        response.json = async () => { oversizedJSONParses++; return {}; };
        return response;
      };
      ui.updateLogs([{ level: 'info', message: 'Previous valid status' }]);
      ui.taskDrawer.classList.add('is-visible');
      ui.renderTaskDrawer({ label: 'Previous valid task', status: 'succeeded', log: 'Previous valid log' });
      await ui.startPolling();
      ui.stopPolling();
      ui.loadTaskDetails(123);
    }, ceiling);
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

    // Exercise the export handler against a real stalled body and late headers.
    await page.clock.install();
    for (const stage of ['headers', 'body']) {
      const selected = await page.evaluate(() => {
        window.requests = [];
        window.toasts = [];
        ui.showToast = (message, level) => toasts.push({ message, level });
        window.fetch = (url, options) => new Promise((resolve) => requests.push({ resolve, options }));
        ui.dataLabForm.dispatchEvent(new Event('submit', { cancelable: true }));
        return ui.activeNotebookSlug;
      });
      // Receiving headers must not reset the total budget.
      await page.clock.fastForward(20000);
      if (stage === 'body') {
        await page.evaluate(() => requests[0].resolve(new Response(new ReadableStream({
          start(controller) { window.lateBody = controller; },
        }))));
      }
      await page.clock.fastForward(9999);
      assert.equal(await page.locator('[data-data-lab-form] button').isDisabled(), true);
      await page.clock.fastForward(1);
      assert.equal(await page.locator('[data-data-lab-form] button').isDisabled(), false);
      assert.equal(await page.evaluate(() => requests[0].options.signal.aborted), true);
      assert.match(await page.evaluate(() => toasts[0].message), /may already have completed.*Inspect.*retry/);
      await page.evaluate((stage) => {
        const payload = JSON.stringify({ data_lab: { selected_notebook: 'obsolete' } });
        if (stage === 'body') {
          lateBody.enqueue(new TextEncoder().encode(payload));
          lateBody.close();
        } else requests[0].resolve(new Response(payload));
      }, stage);
      assert.equal(await page.evaluate(() => ui.activeNotebookSlug), selected);
      assert.equal(await page.evaluate(() => toasts.some((toast) => toast.level === 'success')), false);
      assert.equal(await page.evaluate(() => requests.length), 1, 'no automatic retry after timeout');
    }

    await page.evaluate(() => {
      ui.responseBytes = 64;
      window.exportCancelled = false;
      window.unboundedExportParses = 0;
      window.fetch = async () => {
        const response = new Response(new ReadableStream({
          start(controller) { controller.enqueue(new Uint8Array(65)); },
          cancel() { exportCancelled = true; },
        }), { status: 400, headers: { 'Content-Length': '1' } });
        response.json = async () => { unboundedExportParses++; return {}; };
        return response;
      };
      ui.dataLabForm.dispatchEvent(new Event('submit', { cancelable: true }));
    });
    await page.waitForFunction(() => !ui.dataLabExportPending);
    assert.equal(await page.evaluate(() => exportCancelled), true);
    assert.equal(await page.evaluate(() => unboundedExportParses), 0);
    assert.match(await page.evaluate(() => toasts.at(-1).message), /byte limit.*may already have completed/);
    await page.evaluate(() => {
      window.fetch = async () => new Response(JSON.stringify({ data_lab: {
        selected_notebook: 'small', selected_notebook_url: '/small',
      } }));
      ui.responseBytes = 1024;
      ui.dataLabForm.dispatchEvent(new Event('submit', { cancelable: true }));
    });
    await page.waitForFunction(() => ui.activeNotebookSlug === 'small');
    console.log('Export acknowledgement DOM passed: total deadline, late response fence, bounded error, deliberate retry');
  } finally {
    await browser.close();
  }
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
