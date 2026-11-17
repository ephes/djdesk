const assert = require('node:assert/strict');
const { test } = require('node:test');
const { installSessionPermissionGuards } = require('../boundary-guards');

test('both session permission handlers deny every capability for every requester', () => {
  const handlers = {};
  const session = {
    setPermissionRequestHandler(handler) { handlers.request = handler; },
    setPermissionCheckHandler(handler) { handlers.check = handler; },
  };
  installSessionPermissionGuards(session);
  const destroyed = { get mainFrame() { throw new Error('Object has been destroyed'); } };
  for (const contents of [null, undefined, destroyed, { mainFrame: { url: 'http://127.0.0.1:8000/' } }]) {
    for (const origin of [undefined, '', 'https://remote-docs.invalid', 'http://127.0.0.1:8000']) {
      for (const permission of ['media', 'geolocation', 'notifications', 'fullscreen', 'unknown-future-permission']) {
        const replies = [];
        handlers.request(contents, permission, (allowed) => replies.push(allowed), {
          requestingUrl: origin, isMainFrame: true, mediaTypes: ['audio', 'video'],
        });
        assert.deepEqual(replies, [false], `${origin}: ${permission}`);
        assert.equal(handlers.check(contents, permission, origin, {
          requestingUrl: origin, embeddingOrigin: 'http://127.0.0.1:8000',
        }), false);
      }
    }
  }
  const replies = [];
  handlers.request(null, 'media', (allowed) => replies.push(allowed));
  assert.deepEqual(replies, [false]);
  assert.equal(handlers.check(null, 'media'), false);
});
