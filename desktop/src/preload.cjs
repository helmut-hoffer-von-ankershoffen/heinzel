const { contextBridge, ipcRenderer } = require("electron");

// macOS draws the traffic lights inside the web's 44 px title bar row (window-options.js); elsewhere the native frame stays.
const mac = /^Mac/.test(globalThis.navigator?.platform ?? "");
const titlebar = mac ? Object.freeze({ height: 44, left: 80, right: 0 }) : null;

// The bridge exposes a fixed read operation, a capability label, the audio source choice, the title bar insets and a full-screen listener.
contextBridge.exposeInMainWorld("heinzelDesktop", Object.freeze({
  workspaceAudio: true,
  titlebar,
  lifecycle: () => ipcRenderer.invoke("hz:lifecycle"),
  // System loopback is offered only where Electron main reports it; a choice arms exactly one capture.
  systemAudio: () => ipcRenderer.invoke("hz:system-audio"),
  audioSource: (source) => ipcRenderer.invoke("hz:audio-source", source === "system" ? "system" : "workspace"),
  onFullscreen: (callback) => {
    const listener = (_event, full) => callback(full === true);
    ipcRenderer.on("hz:fullscreen", listener);
    return () => ipcRenderer.removeListener("hz:fullscreen", listener);
  },
}));
