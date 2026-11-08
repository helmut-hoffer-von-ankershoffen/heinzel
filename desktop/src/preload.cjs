const { contextBridge, ipcRenderer } = require("electron");

// macOS draws the traffic lights inside the web's 44 px title bar row (window-options.js); elsewhere the native frame stays.
const mac = /^Mac/.test(globalThis.navigator?.platform ?? "");
const titlebar = mac ? Object.freeze({ height: 44, left: 80, right: 0 }) : null;

// The isolated world owns the IPC call. Page scripts cannot turn a synthetic click or popup into a native open.
if (window.top === window) {
  document.addEventListener("click", (event) => {
    if (!event.isTrusted || !navigator.userActivation.isActive || event.defaultPrevented || event.button !== 0) return;
    const link = event.target instanceof Element && event.target.closest("a[data-hz-external-action]");
    if (!link || link.target !== "_blank") return;
    event.preventDefault();
    void ipcRenderer.invoke("hz:open-external", link.dataset.hzExternalAction, link.href).then((opened) => {
      if (opened) link.dispatchEvent(new Event("hz:external-opened", { bubbles: true }));
    }).catch(() => {});
  }, true);
}

// The bridge exposes fixed product operations, a capability label, the title bar insets and a full-screen listener;
// no raw IPC or credential material reaches the page.
contextBridge.exposeInMainWorld("heinzelDesktop", Object.freeze({
  workspaceAudio: true,
  externalLinks: true,
  titlebar,
  browser: (request) => ipcRenderer.invoke("hz:browser", request),
  lifecycle: () => ipcRenderer.invoke("hz:lifecycle"),
  onFullscreen: (callback) => {
    const listener = (_event, full) => callback(full === true);
    ipcRenderer.on("hz:fullscreen", listener);
    return () => ipcRenderer.removeListener("hz:fullscreen", listener);
  },
}));
