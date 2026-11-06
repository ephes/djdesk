const { execFile } = require('node:child_process');

async function stopBackend(child, {
  platform = process.platform, kill = process.kill, run = execFile,
  graceMs = 2000, reapMs = 1000, pollMs = 25,
  setTimer = setTimeout,
} = {}) {
  if (!child?.pid) return;
  const exited = () => child.exitCode != null || child.signalCode != null;
  let closed = exited();
  const onClose = () => { closed = true; };
  child.once('close', onClose);
  const delay = () => new Promise((resolve) => setTimer(resolve, pollMs));
  const send = (signal) => {
    try { kill(-child.pid, signal); }
    catch (error) { if (error.code !== 'ESRCH') throw error; }
  };
  const groupExists = () => {
    try { kill(-child.pid, 0); return true; }
    catch (error) { if (error.code === 'ESRCH') return false; throw error; }
  };
  try {
    if (platform === 'win32') {
      if (exited()) return;
      // /T needs the live leader to discover descendants; terminate the tree together.
      await new Promise((resolve, reject) => {
        run('taskkill', ['/PID', String(child.pid), '/T', '/F'],
          { windowsHide: true, timeout: graceMs + reapMs }, (error) => {
            if (error) reject(error);
            else resolve();
          });
      });
    } else {
      send('SIGTERM');
      for (let waited = 0; groupExists() && waited < graceMs; waited += pollMs) await delay();
      if (groupExists()) send('SIGKILL');
    }
    // libuv reaps the direct child. Bound the wait even if close is never delivered.
    for (let waited = 0; !closed && waited < reapMs; waited += pollMs) await delay();
    if (!closed) throw new Error('Backend did not close after tree termination');
  } finally {
    child.removeListener('close', onClose);
  }
}

module.exports = { stopBackend };
