const { contextBridge, ipcRenderer } = require("electron");

// Fixed product operations and a capability label only; no raw IPC or credential material reaches the page.
contextBridge.exposeInMainWorld("heinzelDesktop", Object.freeze({
  workspaceAudio: true,
  browser: (request) => ipcRenderer.invoke("hz:browser", request),
  lifecycle: () => ipcRenderer.invoke("hz:lifecycle"),
}));
