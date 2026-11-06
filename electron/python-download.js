const fs = require('node:fs');
const fsp = require('node:fs/promises');
const path = require('node:path');
const https = require('node:https');
const { createHash } = require('node:crypto');
const { Transform } = require('node:stream');
const { pipeline } = require('node:stream/promises');

async function checksum(file, expected) {
  const hash = createHash('sha256');
  for await (const chunk of fs.createReadStream(file)) hash.update(chunk);
  return hash.digest('hex') === expected;
}

// Only a fully downloaded, verified candidate may replace the cache entry.
async function downloadPython(url, destination, expected, {
  get = https.get, maxBytes = 256 * 1024 * 1024, timeoutMs = 120000, maxRedirects = 5,
} = {}) {
  if (!/^[a-f0-9]{64}$/.test(expected || '')) throw new Error('Missing registered SHA256');
  try {
    if (await checksum(destination, expected)) return;
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
  }
  await fsp.mkdir(path.dirname(destination), { recursive: true });
  const directory = await fsp.mkdtemp(path.join(path.dirname(destination), '.python-download-'));
  const candidate = path.join(directory, 'archive');
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(new Error('Python download deadline exceeded')), timeoutMs);
  try {
    for (let redirects = 0; ; redirects++) {
      if (new URL(url).protocol !== 'https:') throw new Error('Python download requires HTTPS');
      const response = await new Promise((resolve, reject) => {
        const request = get(url, { signal: controller.signal }, resolve);
        request.on('error', reject);
      });
      if ([301, 302, 303, 307, 308].includes(response.statusCode) && response.headers.location) {
        response.destroy();
        if (redirects >= maxRedirects) throw new Error('Python download redirect limit exceeded');
        url = new URL(response.headers.location, url).href;
        continue;
      }
      if (response.statusCode !== 200) {
        response.destroy();
        throw new Error(`Python download HTTP ${response.statusCode}`);
      }
      let bytes = 0;
      const hash = createHash('sha256');
      const budget = new Transform({
        transform(chunk, encoding, callback) {
          bytes += chunk.length;
          if (bytes > maxBytes) return callback(new Error('Python download byte limit exceeded'));
          hash.update(chunk);
          callback(null, chunk);
        },
      });
      await pipeline(response, budget, fs.createWriteStream(candidate, { flags: 'wx' }),
        { signal: controller.signal });
      if (hash.digest('hex') !== expected) throw new Error('Python download checksum mismatch');
      await fsp.rename(candidate, destination);
      return;
    }
  } finally {
    clearTimeout(timer);
    await fsp.rm(directory, { recursive: true, force: true });
  }
}

module.exports = { downloadPython };
