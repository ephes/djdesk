const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { test } = require('node:test');

const {
  buildTrustedOrigin,
  createOpenExternalHandler,
  installNavigationGuards,
  isTrustedSender,
  normalizeExternalUrl,
} = require('../boundary-guards');

const TRUSTED = 'http://127.0.0.1:8000';
const silentLogger = { warn() {} };

function fakeShell({ fail = false } = {}) {
  const opened = [];
  return {
    opened,
    openExternal: async (url) => {
      opened.push(url);
      if (fail) {
        throw new Error('no handler');
      }
    },
  };
}

function trustedEvent(url = `${TRUSTED}/dashboard/`) {
  const frame = { url, parent: null };
  return { senderFrame: frame, sender: { mainFrame: frame } };
}

function subFrameEvent(url) {
  const mainFrame = { url: `${TRUSTED}/`, parent: null };
  const frame = { url, parent: mainFrame };
  return { senderFrame: frame, sender: { mainFrame } };
}

function navEvent(url, isMainFrame = true) {
  return {
    url,
    isMainFrame,
    defaultPrevented: false,
    preventDefault() {
      this.defaultPrevented = true;
    },
  };
}

function fakeWebContents() {
  const contents = new EventEmitter();
  contents.setWindowOpenHandler = (handler) => {
    contents.windowOpenHandler = handler;
  };
  return contents;
}

function guardedContents(trustedOrigin = TRUSTED) {
  const shell = fakeShell();
  const contents = fakeWebContents();
  installNavigationGuards(contents, {
    shell,
    getTrustedOrigin: () => trustedOrigin,
    logger: silentLogger,
  });
  return { contents, shell };
}

const flush = () => new Promise((resolve) => setImmediate(resolve));

test('buildTrustedOrigin only accepts real TCP ports', () => {
  assert.equal(buildTrustedOrigin(8000), TRUSTED);
  assert.equal(buildTrustedOrigin('8123'), 'http://127.0.0.1:8123');
  for (const port of [undefined, null, 0, -1, 70000, 'abc', 80.5]) {
    assert.equal(buildTrustedOrigin(port), null);
  }
});

test('normalizeExternalUrl allows only credential-free http(s) URLs', () => {
  assert.equal(normalizeExternalUrl('https://example.com/a?b=1'), 'https://example.com/a?b=1');
  assert.equal(normalizeExternalUrl('http://example.com'), 'http://example.com/');
  for (const url of [
    'file:///etc/passwd',
    'FILE:///etc/passwd',
    'smb://host/share',
    'ms-msdt:/id PCWDiagnostic',
    'djdesk://open',
    'javascript:alert(1)',
    'data:text/html,hi',
    'mailto:someone@example.com',
    'https://user:secret@example.com/',
    'not a url',
    '',
    null,
    undefined,
    42,
    { href: 'https://example.com' },
  ]) {
    assert.equal(normalizeExternalUrl(url), null, String(url));
  }
});

test('open-external dispatches trusted http(s) requests', async () => {
  const shell = fakeShell();
  const handler = createOpenExternalHandler({
    shell,
    getTrustedOrigin: () => TRUSTED,
    logger: silentLogger,
  });
  assert.equal(await handler(trustedEvent(), 'https://djdesk.readthedocs.io/en/latest/'), true);
  assert.equal(await handler(trustedEvent(), 'http://example.com/page'), true);
  assert.deepEqual(shell.opened, [
    'https://djdesk.readthedocs.io/en/latest/',
    'http://example.com/page',
  ]);
});

test('open-external blocks file and custom protocols', async () => {
  const shell = fakeShell();
  const handler = createOpenExternalHandler({
    shell,
    getTrustedOrigin: () => TRUSTED,
    logger: silentLogger,
  });
  for (const url of ['file:///Applications/Calculator.app', 'vscode://x', 'ms-msdt:/id', 'tel:1']) {
    assert.equal(await handler(trustedEvent(), url), false, url);
  }
  assert.deepEqual(shell.opened, []);
});

test('open-external blocks untrusted sender frames', async () => {
  const shell = fakeShell();
  const handler = createOpenExternalHandler({
    shell,
    getTrustedOrigin: () => TRUSTED,
    logger: silentLogger,
  });
  const untrusted = [
    trustedEvent('https://evil.example/'),
    trustedEvent('http://127.0.0.1:9999/'),
    trustedEvent('http://localhost:8000/'),
    trustedEvent('http://127.0.0.1:8000.evil.example/'),
    trustedEvent('file:///tmp/page.html'),
    trustedEvent('about:blank'),
    subFrameEvent('https://djdesk.readthedocs.io/'),
    subFrameEvent(`${TRUSTED}/docs/`),
    { senderFrame: null, sender: {} },
    {},
    undefined,
  ];
  for (const event of untrusted) {
    assert.equal(await handler(event, 'https://example.com/'), false);
  }
  assert.deepEqual(shell.opened, []);
});

test('open-external rejects everything before the Django origin is known', async () => {
  const shell = fakeShell();
  const handler = createOpenExternalHandler({
    shell,
    getTrustedOrigin: () => null,
    logger: silentLogger,
  });
  assert.equal(await handler(trustedEvent(), 'https://example.com/'), false);
  assert.deepEqual(shell.opened, []);
});

test('open-external reports shell failures as false', async () => {
  const handler = createOpenExternalHandler({
    shell: fakeShell({ fail: true }),
    getTrustedOrigin: () => TRUSTED,
    logger: silentLogger,
  });
  assert.equal(await handler(trustedEvent(), 'https://example.com/'), false);
});

test('isTrustedSender requires the top-level trusted frame', () => {
  assert.equal(isTrustedSender(trustedEvent(), TRUSTED), true);
  assert.equal(isTrustedSender(subFrameEvent(`${TRUSTED}/`), TRUSTED), false);
  const other = { url: `${TRUSTED}/`, parent: null };
  assert.equal(
    isTrustedSender({ senderFrame: other, sender: { mainFrame: { url: `${TRUSTED}/` } } }, TRUSTED),
    false
  );
});

test('internal main-frame navigation stays allowed', () => {
  const { contents, shell } = guardedContents();
  for (const name of ['will-navigate', 'will-frame-navigate', 'will-redirect']) {
    const event = navEvent(`${TRUSTED}/workspaces/demo/?tab=1`);
    contents.emit(name, event);
    assert.equal(event.defaultPrevented, false, name);
  }
  assert.deepEqual(shell.opened, []);
});

test('foreign main-frame navigation is blocked and handed to the browser once', async () => {
  const { contents, shell } = guardedContents();
  const url = 'https://evil.example/phish';
  const navigate = navEvent(url);
  const frameNavigate = navEvent(url);
  contents.emit('will-navigate', navigate);
  contents.emit('will-frame-navigate', frameNavigate);
  await flush();
  assert.equal(navigate.defaultPrevented, true);
  assert.equal(frameNavigate.defaultPrevented, true);
  assert.deepEqual(shell.opened, [url]);
});

test('foreign main-frame targets are blocked; only http(s) ones reach the browser', async () => {
  const { contents, shell } = guardedContents();
  for (const url of [
    'file:///etc/passwd',
    'djdesk://x',
    'javascript:alert(1)',
    'data:text/html,<script>1</script>',
    'about:blank',
    'http://127.0.0.1:9999/',
    'http://localhost:8000/',
    'https://127.0.0.1:8000/',
  ]) {
    for (const name of ['will-navigate', 'will-frame-navigate']) {
      const event = navEvent(url);
      contents.emit(name, event);
      assert.equal(event.defaultPrevented, true, `${name} ${url}`);
    }
  }
  await flush();
  assert.deepEqual(shell.opened, ['http://127.0.0.1:9999/', 'http://localhost:8000/', 'https://127.0.0.1:8000/']);
});

test('foreign redirects are blocked and never dispatched', async () => {
  const { contents, shell } = guardedContents();
  for (const url of ['https://evil.example/', 'file:///etc/passwd', 'custom://x']) {
    const event = navEvent(url);
    contents.emit('will-redirect', event);
    assert.equal(event.defaultPrevented, true, url);
  }
  await flush();
  assert.deepEqual(shell.opened, []);
});

test('legacy positional event arguments are honoured', () => {
  const { contents } = guardedContents();
  const blocked = { preventDefault() { this.defaultPrevented = true; } };
  contents.emit('will-navigate', blocked, 'https://evil.example/', false, true);
  assert.equal(blocked.defaultPrevented, true);
  const allowed = { preventDefault() { this.defaultPrevented = true; } };
  contents.emit('will-redirect', allowed, 'https://docs.example/', false, false);
  assert.equal(allowed.defaultPrevented, undefined);
});

test('sub frames may load http(s) docs but not local or custom schemes', async () => {
  const { contents, shell } = guardedContents();
  for (const url of ['https://djdesk.readthedocs.io/', `${TRUSTED}/docs/`, 'about:blank']) {
    for (const name of ['will-frame-navigate', 'will-redirect']) {
      const event = navEvent(url, false);
      contents.emit(name, event);
      assert.equal(event.defaultPrevented, false, `${name} ${url}`);
    }
  }
  for (const url of ['file:///etc/passwd', 'djdesk://x', 'javascript:alert(1)', 'data:text/html,x']) {
    for (const name of ['will-frame-navigate', 'will-redirect']) {
      const event = navEvent(url, false);
      contents.emit(name, event);
      assert.equal(event.defaultPrevented, true, `${name} ${url}`);
    }
  }
  await flush();
  assert.deepEqual(shell.opened, []);
});

test('popups are always denied; http(s) targets open in the browser', async () => {
  const { contents, shell } = guardedContents();
  assert.deepEqual(contents.windowOpenHandler({ url: 'https://example.com/docs' }), { action: 'deny' });
  assert.deepEqual(contents.windowOpenHandler({ url: 'file:///etc/passwd' }), { action: 'deny' });
  assert.deepEqual(contents.windowOpenHandler({ url: 'custom://x' }), { action: 'deny' });
  assert.deepEqual(contents.windowOpenHandler({ url: `${TRUSTED}/admin/` }), { action: 'deny' });
  assert.deepEqual(contents.windowOpenHandler({ url: 'about:blank' }), { action: 'deny' });
  assert.deepEqual(contents.windowOpenHandler({}), { action: 'deny' });
  await flush();
  assert.deepEqual(shell.opened, ['https://example.com/docs']);
});

test('webview attachment is blocked', () => {
  const { contents } = guardedContents();
  const event = navEvent('https://example.com/');
  contents.emit('will-attach-webview', event);
  assert.equal(event.defaultPrevented, true);
});

test('everything is blocked while the trusted origin is unknown', () => {
  const { contents } = guardedContents(null);
  const event = navEvent(`${TRUSTED}/`);
  contents.emit('will-navigate', event);
  assert.equal(event.defaultPrevented, true);
});
