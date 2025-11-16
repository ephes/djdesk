const { contextBridge, ipcRenderer } = require('electron');
const path = require('path');

const WIZARD_STORAGE_KEY = 'djdesk.wizard.projectPath';
const DROP_EVENT = 'djdesk:workspace-drop';
const IPC_CHANNELS = {
  OPEN_EXTERNAL: 'djdesk:open-external',
  NOTIFY: 'djdesk:notify',
};

const hasDataset = (node) => Boolean(node?.dataset);

const sanitizePath = (rawPath) => {
  if (!rawPath || typeof rawPath !== 'string') {
    return null;
  }
  try {
    const normalized = path.normalize(rawPath);
    return normalized || null;
  } catch {
    return null;
  }
};

const stageProjectPath = (rawPath) => {
  const normalized = sanitizePath(rawPath);
  if (!normalized) {
    return null;
  }
  try {
    window.localStorage.setItem(WIZARD_STORAGE_KEY, normalized);
  } catch {
    // Ignore storage failures (e.g., disabled storage)
  }
  return normalized;
};

const getStagedProjectPath = () => {
  try {
    return window.localStorage.getItem(WIZARD_STORAGE_KEY);
  } catch {
    return null;
  }
};

const clearStagedProjectPath = () => {
  try {
    window.localStorage.removeItem(WIZARD_STORAGE_KEY);
  } catch {
    // Ignore cleanup failures
  }
};

const dispatchWorkspaceDrop = (paths) => {
  window.dispatchEvent(new CustomEvent(DROP_EVENT, { detail: { paths } }));
};

const findDropzoneTarget = (event) => {
  const pathTargets = typeof event.composedPath === 'function' ? event.composedPath() : [];
  return pathTargets.find((node) => hasDataset(node) && node.dataset.dropzone !== undefined);
};

window.addEventListener('dragover', (event) => {
  if (!findDropzoneTarget(event)) {
    return;
  }
  event.preventDefault();
  if (event.dataTransfer) {
    event.dataTransfer.dropEffect = 'copy';
  }
});

window.addEventListener('drop', (event) => {
  const target = findDropzoneTarget(event);
  if (!target) {
    return;
  }
  event.preventDefault();
  const files = Array.from(event.dataTransfer?.files ?? []);
  const sanitized = files
    .map((file) => file?.path || '')
    .map(stageProjectPath)
    .filter(Boolean);
  if (sanitized.length) {
    dispatchWorkspaceDrop(sanitized);
  }
});

contextBridge.exposeInMainWorld('djdeskNative', {
  isElectron: true,
  version: process.versions.electron,
  openExternal: (url) => ipcRenderer.invoke(IPC_CHANNELS.OPEN_EXTERNAL, url),
  notify: (options) => ipcRenderer.invoke(IPC_CHANNELS.NOTIFY, options),
  stageProjectPath,
  getStagedProjectPath,
  clearStagedProjectPath,
});
