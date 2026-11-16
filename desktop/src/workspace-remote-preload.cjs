const { contextBridge } = require("electron");
// Appearance only: a remote workspace never receives IPC or local native capabilities.
contextBridge.exposeInMainWorld("heinzelDesktop", Object.freeze({
  remoteWorkspace: true,
  titlebar: (globalThis.navigator?.platform ?? "").startsWith("Mac") ? Object.freeze({ height: 44, left: 80, right: 0 }) : null,
}));
