// The import dialog's only bridge: one channel, fixed requests. It carries names and counts, never a cookie value.
const { contextBridge, ipcRenderer } = require("electron");
const ask = (request) => ipcRenderer.invoke("hz:cookie-import", request);
contextBridge.exposeInMainWorld("cookieImport", Object.freeze({
  profiles: () => ask({ action: "profiles" }),
  sites: (profile) => ask({ action: "sites", profile }),
  import: (profile, sites, persistent) => ask({ action: "import", profile, sites: [...sites], persistent: persistent === true }),
  imported: () => ask({ action: "imported" }),
  remove: (site) => ask({ action: "remove", site }),
  removeAll: () => ask({ action: "remove-all" }),
  close: () => ipcRenderer.invoke("hz:cookie-import-close"),
  onView: (callback) => ipcRenderer.on("hz:cookie-import-view", (_event, view) => callback(view === "manage" ? "manage" : "import")),
}));
