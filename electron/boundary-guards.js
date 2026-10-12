/**
 * Native boundary guards for the DJDesk Electron shell.
 *
 * The privileged window is only ever meant to display the local Django
 * server. Everything in this module is dependency-injected so it can be
 * exercised with plain `node --test` without launching Electron.
 */

const EXTERNAL_PROTOCOLS = new Set(['http:', 'https:']);

function parseUrl(value) {
  if (!value || typeof value !== 'string') {
    return null;
  }
  try {
    return new URL(value);
  } catch {
    return null;
  }
}

/**
 * Return the exact origin (scheme + host + port) of the running Django
 * server, or null when the port is not a usable TCP port.
 */
function buildTrustedOrigin(port) {
  const numeric = Number(port);
  if (!Number.isInteger(numeric) || numeric < 1 || numeric > 65535) {
    return null;
  }
  return `http://127.0.0.1:${numeric}`;
}

/**
 * True only when `url` is an http(s) URL whose origin equals `trustedOrigin`.
 */
function isTrustedUrl(url, trustedOrigin) {
  if (!trustedOrigin) {
    return false;
  }
  const parsed = parseUrl(url);
  if (!parsed || !EXTERNAL_PROTOCOLS.has(parsed.protocol)) {
    return false;
  }
  return parsed.origin === trustedOrigin;
}

/**
 * Normalize a URL destined for the OS handler. Only http(s) URLs without
 * embedded credentials are allowed; everything else returns null.
 */
function normalizeExternalUrl(url) {
  const parsed = parseUrl(url);
  if (!parsed || !EXTERNAL_PROTOCOLS.has(parsed.protocol)) {
    return null;
  }
  if (!parsed.hostname || parsed.username || parsed.password) {
    return null;
  }
  return parsed.toString();
}

/**
 * True when the IPC invocation comes from the top-level frame of the trusted
 * window and that frame currently displays the trusted origin.
 */
function isTrustedSender(event, trustedOrigin) {
  const frame = event?.senderFrame;
  if (!frame || typeof frame.url !== 'string') {
    return false;
  }
  if (frame.parent) {
    return false;
  }
  const mainFrame = event.sender?.mainFrame;
  if (mainFrame && mainFrame !== frame) {
    return false;
  }
  return isTrustedUrl(frame.url, trustedOrigin);
}

/**
 * Open `url` in the system browser when it is a plain http(s) URL.
 * Resolves to true only when the URL was dispatched.
 */
async function openExternalSafely(shell, url, logger = console) {
  const normalized = normalizeExternalUrl(url);
  if (!normalized) {
    logger.warn('Blocked external URL dispatch (only http/https allowed)');
    return false;
  }
  try {
    await shell.openExternal(normalized);
    return true;
  } catch (error) {
    logger.warn('Failed to open external URL', error);
    return false;
  }
}

/**
 * Build the `djdesk:open-external` IPC handler.
 */
function createOpenExternalHandler({ shell, getTrustedOrigin, logger = console }) {
  return async (event, url) => {
    if (!isTrustedSender(event, getTrustedOrigin())) {
      logger.warn('Blocked open-external request from untrusted frame');
      return false;
    }
    return openExternalSafely(shell, url, logger);
  };
}

function eventUrl(event, legacyUrl) {
  return typeof event?.url === 'string' ? event.url : legacyUrl;
}

function eventIsMainFrame(event, legacyIsMainFrame) {
  if (typeof event?.isMainFrame === 'boolean') {
    return event.isMainFrame;
  }
  if (typeof legacyIsMainFrame === 'boolean') {
    return legacyIsMainFrame;
  }
  // Unknown frame: treat it as the privileged top-level frame.
  return true;
}

/**
 * Decide what to do with a navigation or redirect target.
 *
 * - main frame: only the trusted origin may load.
 * - sub frames (docs drawer): trusted origin or plain http(s) content; they
 *   never receive the preload bridge and fail the IPC sender check.
 */
function isNavigationAllowed(url, isMainFrame, trustedOrigin) {
  if (isTrustedUrl(url, trustedOrigin)) {
    return true;
  }
  if (isMainFrame) {
    return false;
  }
  const parsed = parseUrl(url);
  if (!parsed) {
    return false;
  }
  if (parsed.protocol === 'about:') {
    return parsed.href === 'about:blank' || parsed.href === 'about:srcdoc';
  }
  return normalizeExternalUrl(url) !== null;
}

/**
 * Attach navigation, redirect, popup, and webview restrictions to a
 * webContents instance.
 */
function installNavigationGuards(webContents, { shell, getTrustedOrigin, logger = console }) {
  const guard = (kind, dispatchExternally) => (event, legacyUrl, _isInPlace, legacyIsMainFrame) => {
    const url = eventUrl(event, legacyUrl);
    const isMainFrame = eventIsMainFrame(event, legacyIsMainFrame);
    if (isNavigationAllowed(url, isMainFrame, getTrustedOrigin())) {
      return;
    }
    event.preventDefault();
    logger.warn(`Blocked ${isMainFrame ? 'window' : 'frame'} ${kind} outside the DJDesk origin`);
    if (dispatchExternally && isMainFrame) {
      // A user-facing link to the wider web belongs in the system browser.
      void openExternalSafely(shell, url, logger);
    }
  };

  // `will-navigate` only fires for the main frame and is the single place
  // that hands a blocked link to the system browser. `will-frame-navigate`
  // fires for every frame and only blocks, so nothing is dispatched twice.
  webContents.on('will-navigate', guard('navigation', true));
  webContents.on('will-frame-navigate', guard('navigation', false));
  webContents.on('will-redirect', guard('redirect', false));
  webContents.on('will-attach-webview', (event) => {
    event.preventDefault();
    logger.warn('Blocked <webview> attachment');
  });

  webContents.setWindowOpenHandler((details) => {
    const url = details?.url;
    if (!isTrustedUrl(url, getTrustedOrigin())) {
      void openExternalSafely(shell, url, logger);
    }
    return { action: 'deny' };
  });
}

module.exports = {
  buildTrustedOrigin,
  createOpenExternalHandler,
  installNavigationGuards,
  isNavigationAllowed,
  isTrustedSender,
  isTrustedUrl,
  normalizeExternalUrl,
  openExternalSafely,
};
