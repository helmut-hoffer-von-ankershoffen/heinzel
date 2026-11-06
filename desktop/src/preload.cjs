const { contextBridge, ipcRenderer } = require("electron");

// The bridge exposes a fixed read operation and a capability label.
contextBridge.exposeInMainWorld("heinzelDesktop", Object.freeze({
  workspaceAudio: true,
  lifecycle: () => ipcRenderer.invoke("hz:lifecycle"),
}));
