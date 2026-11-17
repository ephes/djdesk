const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const { _electron: electron } = require('playwright-core');

async function serve(html) {
  const server = http.createServer((_request, response) => {
    response.writeHead(200, { 'Content-Type': 'text/html' });
    response.end(html);
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  return { server, origin: `http://127.0.0.1:${server.address().port}` };
}

async function main() {
  const profile = await fs.mkdtemp(path.join(os.tmpdir(), 'djdesk-frames-'));
  const servers = [];
  let app;
  try {
    const remote = await serve('<h1>Remote synthetic documentation</h1>');
    servers.push(remote.server);
    const trusted = await serve(`<h1>Synthetic DJDesk</h1>
      <iframe src="${remote.origin}/docs" allow="camera; microphone; geolocation"></iframe>`);
    servers.push(trusted.server);
    app = await electron.launch({
      args: [path.join(__dirname, 'frame-boundaries-app.cjs'), `--user-data-dir=${profile}`],
      env: { ...process.env, DJDESK_TEST_APP_ORIGIN: trusted.origin,
        XDG_CONFIG_HOME: profile, XDG_CACHE_HOME: profile },
      timeout: 30000,
    });
    const page = await app.firstWindow();
    await page.waitForURL(`${trusted.origin}/`);
    await page.frameLocator('iframe').locator('h1').waitFor();
    const frame = page.frame({ url: `${remote.origin}/docs` });
    assert.equal(await frame.locator('h1').textContent(), 'Remote synthetic documentation');
    assert.equal(await frame.evaluate(() => typeof window.djdeskNative), 'undefined');
    const result = await frame.evaluate(async () => {
      const states = {};
      for (const name of ['camera', 'microphone', 'geolocation', 'notifications']) {
        states[name] = (await navigator.permissions.query({ name })).state;
      }
      const media = [];
      for (const constraints of [{ audio: true }, { video: true }]) {
        try {
          const stream = await navigator.mediaDevices.getUserMedia(constraints);
          stream.getTracks().forEach((track) => track.stop());
          media.push('granted');
        } catch (error) { media.push(error.name); }
      }
      const geolocation = await new Promise((resolve) => navigator.geolocation.getCurrentPosition(
        () => resolve('granted'), (error) => resolve(error.code), { timeout: 5000 }
      ));
      return { states, media, geolocation, notification: await Notification.requestPermission() };
    });
    assert.deepEqual(result.states, {
      camera: 'denied', microphone: 'denied', geolocation: 'denied', notifications: 'denied',
    });
    assert.deepEqual(result.media, ['NotAllowedError', 'NotAllowedError']);
    assert.equal(result.geolocation, 1); // PERMISSION_DENIED
    assert.equal(result.notification, 'denied');
    const receipt = await app.evaluate(() => global.frameFixture);
    assert.ok(receipt.checks.some((entry) => entry.origin.startsWith(remote.origin)));
    assert.ok(receipt.requests.some((entry) => entry.permission === 'media'));
    assert.equal(await page.evaluate(() => window.djdeskNative.notify({
      title: 'Synthetic task', body: 'Done',
    })), true);
    assert.deepEqual(await app.evaluate(() => global.frameFixture.notifications), [
      { title: 'Synthetic task', body: 'Done' },
    ]);
    console.log('Electron remote iframe passed: docs load, capabilities denied, native bridge retained');
  } finally {
    try { await app?.close(); }
    finally {
      await Promise.all(servers.map((server) => new Promise((resolve) => server.close(resolve))));
      await fs.rm(profile, { recursive: true, force: true });
    }
  }
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
