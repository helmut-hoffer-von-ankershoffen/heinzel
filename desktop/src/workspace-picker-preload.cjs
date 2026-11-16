const { contextBridge, ipcRenderer } = require("electron");
contextBridge.exposeInMainWorld("workspacePicker", Object.freeze({
  state: () => ipcRenderer.invoke("hz:workspace", { kind: "state" }),
  connect: (link) => ipcRenderer.invoke("hz:workspace", { kind: "link", link }),
  local: () => ipcRenderer.invoke("hz:workspace", { kind: "local" }),
  recent: (origin) => ipcRenderer.invoke("hz:workspace", { kind: "recent", origin }),
  forget: (origin) => ipcRenderer.invoke("hz:workspace", { kind: "forget", origin }),
  retry: () => ipcRenderer.invoke("hz:workspace", { kind: "retry" }),
  back: () => ipcRenderer.invoke("hz:workspace", { kind: "back" }),
  onState: (callback) => ipcRenderer.on("hz:workspace-state", (_event, state) => callback(state)),
}));
