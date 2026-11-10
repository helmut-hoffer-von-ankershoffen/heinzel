const { contextBridge, ipcRenderer } = require("electron");

// macOS draws the traffic lights inside the web's 44 px title bar row (window-options.js); elsewhere the native frame stays.
const mac = /^Mac/.test(globalThis.navigator?.platform ?? "");
const titlebar = mac ? Object.freeze({ height: 44, left: 80, right: 0 }) : null;

// The bridge exposes fixed product operations, a capability label, the title bar insets and a full-screen listener;
// no raw IPC or credential material reaches the page.
contextBridge.exposeInMainWorld("heinzelDesktop", Object.freeze({
  workspaceAudio: true,
  titlebar,
  browser: (request) => ipcRenderer.invoke("hz:browser", request),
  lifecycle: () => ipcRenderer.invoke("hz:lifecycle"),
  about: () => ipcRenderer.invoke("hz:about"),
  onFullscreen: (callback) => {
    const listener = (_event, full) => callback(full === true);
    ipcRenderer.on("hz:fullscreen", listener);
    return () => ipcRenderer.removeListener("hz:fullscreen", listener);
  },
}));
