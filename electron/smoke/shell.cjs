const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { _electron: electron } = require('playwright-core');

async function main() {
  if (!process.argv[2]) throw new Error('Pass the unpacked app executable to npm run smoke');
  const profile = await fs.mkdtemp(path.join(os.tmpdir(), 'djdesk-shell-smoke-'));
  let app;
  try {
    app = await electron.launch({
      executablePath: path.resolve(process.argv[2]),
      args: [`--user-data-dir=${profile}`, ...process.argv.slice(3)],
      env: { ...process.env, XDG_CONFIG_HOME: profile, XDG_CACHE_HOME: profile },
      timeout: 90000,
    });
    assert.equal(await app.evaluate(({ app }) => app.isPackaged), true);
    const window = await app.firstWindow({ timeout: 90000 });
    await window.waitForURL(/^http:\/\/127\.0\.0\.1:\d+\/$/);
    await window.waitForFunction(() => window.djdeskNative?.isElectron);
    const result = await window.evaluate(() => ({
      native: window.djdeskNative.isElectron,
      version: window.djdeskNative.version,
      node: typeof require,
    }));
    assert.equal(result.native, true);
    assert.equal(result.node, 'undefined');
    assert.match(result.version, /^45\./);
    console.log('Unpacked shell smoke passed: bundled backend, isolated renderer and preload');
  } finally {
    try { await app?.close(); }
    finally { await fs.rm(profile, { recursive: true, force: true }); }
  }
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
