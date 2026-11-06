const http = require('node:http');

function waitForBackend(url, {
  signal, get = http.get, maxAttempts = 30, initialDelayMs = 1000,
  retryMs = 500, requestTimeoutMs = 2000, deadlineMs = 76000,
  setTimer = setTimeout, clearTimer = clearTimeout,
} = {}) {
  return new Promise((resolve, reject) => {
    let done = false;
    let attempts = 0;
    let retry;
    let attemptTimer;
    let deadline;
    let request;
    const finish = (error) => {
      if (done) return;
      done = true;
      clearTimer(retry);
      clearTimer(attemptTimer);
      clearTimer(deadline);
      signal?.removeEventListener('abort', abort);
      request?.destroy();
      if (error) reject(error);
      else resolve();
    };
    const abort = () => finish(new Error('Backend startup cancelled'));
    const attempt = () => {
      if (done) return;
      attempts++;
      let settled = false;
      const settle = (healthy) => {
        if (done || settled) return;
        settled = true;
        clearTimer(attemptTimer);
        request?.destroy();
        request = null;
        if (healthy) finish();
        else if (attempts >= maxAttempts) finish(new Error('Backend startup attempt limit exceeded'));
        else retry = setTimer(attempt, retryMs);
      };
      try {
        request = get(url, (response) => {
          response.on('error', () => settle(false));
          response.resume();
          response.destroy();
          settle(response.statusCode === 200);
        });
        request.on('error', () => settle(false));
        attemptTimer = setTimer(() => settle(false), requestTimeoutMs);
      } catch (error) {
        settle(false);
      }
    };
    if (signal?.aborted) return abort();
    signal?.addEventListener('abort', abort, { once: true });
    deadline = setTimer(() => finish(new Error('Backend startup deadline exceeded')), deadlineMs);
    retry = setTimer(attempt, initialDelayMs);
  });
}

module.exports = { waitForBackend };
