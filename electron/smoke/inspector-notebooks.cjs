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
    await page.route('**/*', (route) => route.abort());
    await page.setContent(html);
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
  } finally {
    await browser.close();
  }
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
