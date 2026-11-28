const { contextBridge, ipcRenderer } = require("electron");
// Runtime labels and appearance only; remote pages have no native commands.
contextBridge.exposeInMainWorld("heinzelDesktop", Object.freeze({
  workspaces: Object.freeze({
    currentName: () => ipcRenderer.invoke("hz:workspace-menu", "name"),
    open: () => ipcRenderer.invoke("hz:workspace-menu", "open"),
  }),
  clientFlavor: "electron",
  clientOS: { darwin: "macOS", win32: "Windows", linux: "Linux" }[globalThis.process?.platform],
  remoteWorkspace: true,
  titlebar: (globalThis.navigator?.platform ?? "").startsWith("Mac") ? Object.freeze({ height: 44, left: 80, right: 0 }) : null,
}));
