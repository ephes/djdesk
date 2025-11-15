/**
 * Preload script for Electron
 *
 * This script runs before the renderer process loads and provides
 * a secure bridge between the main process and web content.
 *
 * With contextIsolation enabled, this is the only way to safely
 * expose APIs to the renderer.
 */

const { contextBridge } = require('electron');

// For now, we don't need to expose any APIs to the renderer
// The Django app runs entirely in the web context
// Future enhancements can add APIs here as needed

contextBridge.exposeInMainWorld('electron', {
  // Placeholder for future Electron-specific APIs
  version: process.versions.electron
});
