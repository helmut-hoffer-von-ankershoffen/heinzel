const { contextBridge, ipcRenderer } = require("electron");
if (process.isMainFrame && location.protocol === "file:" && location.pathname.endsWith("/player-chrome.html")) contextBridge.exposeInMainWorld("playerChrome", Object.freeze({
  command: (request) => ipcRenderer.invoke("hz:player-chrome", request),
}));
