const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

test('sandbox preload loads using only Electron and stages native dropped paths', () => {
  const events = new Map();
  const storage = new Map();
  let bridge;
  const context = {
    require(name) {
      assert.equal(name, 'electron', 'sandbox cannot require Node path');
      return {
        contextBridge: { exposeInMainWorld(name, value) { bridge = value; } },
        ipcRenderer: { invoke() {} },
        webUtils: { getPathForFile: (file) => file.syntheticPath || '' },
      };
    },
    process: { versions: { electron: '45.2.3' } },
    window: {
      addEventListener: (name, callback) => events.set(name, callback),
      dispatchEvent() {},
      localStorage: {
        setItem: (name, value) => storage.set(name, value),
        getItem: (name) => storage.get(name),
      },
    },
    CustomEvent: class {},
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '..', 'preload.cjs'), 'utf8'), context);
  assert.equal(bridge.isElectron, true);
  assert.equal(bridge.stageProjectPath(null), null);
  for (const nativePath of ['/synthetic/workspace', 'C:\\synthetic\\workspace']) {
    events.get('drop')({
      composedPath: () => [{ dataset: { dropzone: '' } }],
      preventDefault() {},
      dataTransfer: { files: [{ syntheticPath: nativePath }] },
    });
    assert.equal(bridge.getStagedProjectPath(), nativePath);
  }
});
