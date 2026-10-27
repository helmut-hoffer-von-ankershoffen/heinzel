const { contextBridge, ipcRenderer } = require("electron");

// A single fixed read operation; credential material never reaches the page.
contextBridge.exposeInMainWorld("heinzelDesktop", Object.freeze({
  lifecycle: () => ipcRenderer.invoke("hz:lifecycle"),
}));
