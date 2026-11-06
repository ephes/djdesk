const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const { once } = require('node:events');
const path = require('node:path');
const { setTimeout: delay } = require('node:timers/promises');
const { test } = require('node:test');
const { stopBackend } = require('../backend-shutdown');

test('native shutdown stops a synthetic backend and TERM-ignoring descendant', { timeout: 15000 }, async (t) => {
  const backend = spawn(process.execPath, [path.join(__dirname, 'fixtures', 'backend-tree.cjs')], {
    detached: true, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe', 'ipc'],
  });
  let helperPid;
  let helperGone = false;
  t.after(() => {
    for (const pid of [helperGone ? null : helperPid, backend.exitCode === null && backend.signalCode === null ? backend.pid : null]) {
      if (!pid) continue;
      try { process.kill(pid, 'SIGKILL'); } catch (error) {
        if (error.code !== 'ESRCH') throw error;
      }
    }
  });
  [{ pid: helperPid }] = await once(backend, 'message', { signal: AbortSignal.timeout(5000) });
  process.kill(helperPid, 0); // The fixture actually spawned its helper.
  const closed = once(backend, 'close');
  await stopBackend(backend, { graceMs: 100, reapMs: 3000 });
  await closed;
  let gone = false;
  for (let attempt = 0; attempt < 300; attempt++) {
    try { process.kill(helperPid, 0); }
    catch (error) {
      if (error.code !== 'ESRCH') throw error;
      gone = true; break;
    }
    await delay(10);
  }
  helperGone = gone;
  assert.equal(gone, true, 'owned descendant must not survive shutdown');
});
