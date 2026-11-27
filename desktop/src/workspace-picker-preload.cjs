const { contextBridge, ipcRenderer } = require("electron");
const request = (kind, fields) => ipcRenderer.invoke("hz:workspace", { kind, ...fields });
contextBridge.exposeInMainWorld("workspacePicker", Object.freeze({
  state: () => request("state"), information: (show) => request("information", { show }), edit: () => request("edit"), cancel: () => request("cancel"),
  connect: (link, origin) => request("link", { link, origin }), paste: () => request("paste"),
  local: () => request("local"), recent: (origin) => request("recent", { origin }),
  disconnect: (origin) => request("disconnect", { origin }), forget: (origin) => request("forget", { origin }),
  pause: (origin) => request("pause", { origin }), resume: (origin) => request("resume", { origin }),
  notifications: () => request("notifications"),
  network: () => request("network"), retry: () => request("retry"), back: () => request("back"),
  onState: (callback) => ipcRenderer.on("hz:workspace-state", (_event, state) => callback(state)),
}));
