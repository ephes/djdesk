const fs = require('node:fs');
const fsp = require('node:fs/promises');
const path = require('node:path');
const https = require('node:https');
const { createHash } = require('node:crypto');
const { Transform } = require('node:stream');
const { pipeline } = require('node:stream/promises');

async function* readPythonArchive(file, { maxBytes, signal }) {
  signal.throwIfAborted();
  const before = await fsp.lstat(file);
  if (!before.isFile()) throw new Error('Python cache must be a regular file, not a symbolic link');
  const handle = await fsp.open(file, fs.constants.O_RDONLY |
    (fs.constants.O_NOFOLLOW || 0) | (fs.constants.O_NONBLOCK || 0));
  try {
    const admitted = await handle.stat();
    const sameFile = (stat) => stat.isFile() && stat.dev === admitted.dev &&
      stat.ino === admitted.ino && stat.size === admitted.size &&
      stat.mtimeMs === admitted.mtimeMs && stat.ctimeMs === admitted.ctimeMs;
    if (!sameFile(before)) throw new Error('Python cache changed during admission');
    if (admitted.size > maxBytes) throw new Error('Python cache byte limit exceeded');
    let bytes = 0;
    for await (const chunk of handle.createReadStream({ autoClose: false, signal,
      highWaterMark: Math.min(65536, maxBytes + 1) })) {
      signal.throwIfAborted();
      bytes += chunk.length;
      if (bytes > maxBytes) throw new Error('Python cache byte limit exceeded');
      yield chunk;
    }
    signal.throwIfAborted();
    if (!sameFile(await handle.stat()) || !sameFile(await fsp.lstat(file))) {
      throw new Error('Python cache changed during validation');
    }
    if (bytes !== admitted.size) throw new Error('Python cache read length mismatch');
  } finally {
    await handle.close();
  }
}

async function checksum(file, expected, limits) {
  const hash = createHash('sha256');
  for await (const chunk of readPythonArchive(file, limits)) {
    hash.update(chunk);
  }
  return hash.digest('hex') === expected;
}

async function snapshotPythonArchive(source, snapshot, limits) {
  await pipeline(readPythonArchive(source, limits),
    fs.createWriteStream(snapshot, { flags: 'wx', mode: 0o600 }),
    { signal: limits.signal });
}

// Matches the unsupported-sync set in src/djdesk/inspector/filesystem.py.
const UNSUPPORTED_DIRECTORY_SYNC = new Set(['EINVAL', 'ENOTSUP', 'EOPNOTSUPP', 'ENOSYS', 'ENOTTY']);

async function syncCacheDirectory(destination) {
  // Windows uses file sync only.
  if (process.platform === 'win32') return;
  try {
    const handle = await fsp.open(path.dirname(destination), 'r');
    try {
      await handle.sync();
    } catch (error) {
      if (!UNSUPPORTED_DIRECTORY_SYNC.has(error.code)) throw error;
    } finally {
      await handle.close();
    }
  } catch (error) {
    throw new Error(`Verified Python cache is visible at ${destination}, but its durability is uncertain`,
      { cause: error });
  }
}

// Only a fully downloaded, verified candidate may replace the cache entry.
async function downloadPython(url, destination, expected, {
  get = https.get, maxBytes = 256 * 1024 * 1024, timeoutMs = 120000, maxRedirects = 5,
} = {}) {
  if (!/^[a-f0-9]{64}$/.test(expected || '')) throw new Error('Missing registered SHA256');
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(new Error('Python download deadline exceeded')), timeoutMs);
  let directory, failure;
  let published = false;
  try {
    let cached = false;
    try {
      cached = await checksum(destination, expected, { maxBytes, signal: controller.signal });
    } catch (error) {
      if (error.code !== 'ENOENT') throw error;
    }
    controller.signal.throwIfAborted();
    if (cached) {
      await syncCacheDirectory(destination);
      return;
    }
    const parent = path.dirname(destination);
    await fsp.mkdir(parent, { recursive: true });
    directory = await fsp.mkdtemp(path.join(parent, '.python-download-'));
    const candidate = path.join(directory, 'archive');
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
      const handle = await fsp.open(candidate, 'r+');
      try {
        await handle.sync();
      } finally {
        await handle.close();
      }
      controller.signal.throwIfAborted();
      await fsp.rename(candidate, destination);
      await syncCacheDirectory(destination);
      published = true;
      return;
    }
  } catch (error) {
    failure = error;
    throw error;
  } finally {
    clearTimeout(timer);
    if (directory) {
      try {
        await fsp.rm(directory, { recursive: true, force: true });
      } catch (error) {
        const cleanup = `Python download staging cleanup failed (${directory}): ${error.message}`;
        if (published) {
          console.warn(`Verified Python cache published at ${destination}; ${cleanup}`);
        } else {
          failure.message += `; ${cleanup}`;
        }
      }
    }
  }
}

module.exports = { downloadPython, checksum, snapshotPythonArchive };
