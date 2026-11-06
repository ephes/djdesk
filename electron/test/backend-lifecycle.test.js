const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { test } = require('node:test');
const { waitForBackend } = require('../backend-readiness');
const { stopBackend } = require('../backend-shutdown');

function clock() {
  let now = 0;
  let next = 0;
  const timers = new Map();
  return {
    setTimer: (callback, ms) => { const id = ++next; timers.set(id, { at: now + ms, callback }); return id; },
    clearTimer: (id) => timers.delete(id),
    tick(ms) {
      const end = now + ms;
      while (true) {
        const entry = [...timers].sort((a, b) => a[1].at - b[1].at)[0];
        if (!entry || entry[1].at > end) break;
        timers.delete(entry[0]); now = entry[1].at; entry[1].callback();
      }
      now = end;
    },
    count: () => timers.size,
  };
}

function probes() {
  const requests = [];
  return {
    requests,
    get(url, callback) {
      const request = new EventEmitter();
      request.destroy = () => { request.destroyed = true; request.emit('error', new Error('destroyed')); };
      request.respond = (statusCode) => callback(Object.assign(new EventEmitter(), { statusCode, resume() {}, destroy() {} }));
      requests.push(request);
      return request;
    },
  };
}

test('500, timeout plus error and late callbacks have exactly one retry owner', async () => {
  const time = clock(); const source = probes();
  let opened = 0;
  const ready = waitForBackend('http://synthetic.invalid/', { ...time, ...source })
    .then(() => { opened++; });
  time.tick(1000); source.requests[0].respond(500);
  assert.equal(opened, 0); time.tick(500);
  assert.equal(source.requests.length, 2);
  time.tick(2000); source.requests[1].emit('error', new Error('late error'));
  source.requests[0].respond(200);
  time.tick(500); assert.equal(source.requests.length, 3);
  source.requests[2].respond(200); await ready;
  source.requests[2].respond(200); time.tick(100000);
  assert.equal(opened, 1); assert.equal(source.requests.length, 3); assert.equal(time.count(), 0);
});

test('attempt ceiling and total deadline each stop all probes', async () => {
  for (const options of [{ maxAttempts: 3 }, { maxAttempts: 1000, deadlineMs: 3100 }]) {
    const time = clock(); const source = probes();
    const ready = waitForBackend('http://synthetic.invalid/', { ...time, ...source, ...options });
    time.tick(100000);
    await assert.rejects(ready, /attempt limit|deadline/);
    assert.ok(source.requests.length <= 3);
    assert.equal(time.count(), 0);
  }
});

test('abort cancels before first probe or during a request and ignores late success', async () => {
  for (const elapsed of [0, 1000]) {
    const time = clock(); const source = probes(); const controller = new AbortController();
    const ready = waitForBackend('http://synthetic.invalid/', { ...time, ...source, signal: controller.signal });
    time.tick(elapsed); controller.abort();
    await assert.rejects(ready, /cancelled/);
    source.requests[0]?.respond(200); time.tick(100000);
    assert.equal(time.count(), 0); assert.ok(source.requests.length <= 1);
  }
});

function child() {
  const process = new EventEmitter(); process.pid = 1234; process.exitCode = null;
  return process;
}

test('POSIX shutdown owns the whole group even when leader closes before descendant', async () => {
  const backend = child(); const signals = []; let alive = true;
  const stop = stopBackend(backend, {
    platform: 'linux', graceMs: 20, pollMs: 10,
    setTimer: (callback) => { queueMicrotask(callback); },
    kill(pid, signal) {
      assert.equal(pid, -1234);
      if (!alive) { const error = new Error('gone'); error.code = 'ESRCH'; throw error; }
      if (signal !== 0) signals.push(signal);
      if (signal === 'SIGTERM') backend.emit('close', 0); // helper ignores TERM
      if (signal === 'SIGKILL') alive = false;
    },
  });
  await stop;
  assert.deepEqual(signals, ['SIGTERM', 'SIGKILL']);
  assert.equal(backend.listenerCount('close'), 0);
});

test('normal shutdown returns without waiting a grace period', async () => {
  const backend = child(); let alive = true; let waits = 0;
  await stopBackend(backend, {
    platform: 'darwin',
    setTimer() { waits++; throw new Error('normal shutdown should already be closed'); },
    kill(pid, signal) {
      if (signal === 'SIGTERM') { alive = false; backend.emit('close', 0); }
      if (!alive && signal === 0) { const error = new Error(); error.code = 'ESRCH'; throw error; }
    },
  });
  assert.equal(waits, 0);
});

test('Windows tree termination uses bounded taskkill and waits for close', async () => {
  const backend = child(); let command;
  await stopBackend(backend, {
    platform: 'win32',
    run(...args) {
      command = args.slice(0, 3); backend.emit('close', 0); args[3](null);
    },
    kill() { throw new Error('POSIX kill must not run on Windows'); },
  });
  assert.deepEqual(command, ['taskkill', ['/PID', '1234', '/T', '/F'],
    { windowsHide: true, timeout: 3000 }]);
});

test('termination or reaping failure is reported with bounded waits', async () => {
  const backend = child(); let waits = 0;
  await assert.rejects(stopBackend(backend, {
    platform: 'linux', graceMs: 10, reapMs: 10, pollMs: 5,
    kill() {},
    setTimer(callback) { waits++; queueMicrotask(callback); },
  }), /did not close/);
  assert.equal(waits, 4);
  await assert.rejects(stopBackend(backend, {
    platform: 'win32', run(...args) { args[3](new Error('taskkill failed')); },
  }), /taskkill failed/);
});

test('Windows skips an already exited or signal-killed leader', async () => {
  for (const outcome of [{ exitCode: 0 }, { signalCode: 'SIGTERM' }]) {
    const backend = Object.assign(child(), outcome);
    await stopBackend(backend, {
      platform: 'win32',
      run() { throw new Error('cannot discover a tree after its leader exits'); },
      setTimer() { throw new Error('already reaped'); },
    });
  }
});

test('Windows reports partial tree termination failure even after leader exit', async () => {
  const backend = child();
  await assert.rejects(stopBackend(backend, {
    platform: 'win32',
    run(...args) {
      backend.exitCode = 1;
      backend.emit('close', 1);
      args[3](new Error('synthetic taskkill descendant failure'));
    },
  }), /descendant failure/);
});
