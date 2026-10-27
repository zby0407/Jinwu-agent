// First-run setup bridge. This preload is only attached to the local file://
// setup window; the remote WebUI never gets an Electron API.
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('jinwuSetup', {
  getStatus: () => ipcRenderer.invoke('setup:get-status'),
  save: (payload) => ipcRenderer.invoke('setup:save', payload),
  cancel: () => ipcRenderer.invoke('setup:cancel'),
});
