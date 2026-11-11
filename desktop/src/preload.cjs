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

// A trusted native click is required; renderer scripts receive no callable external-opening bridge.
document.addEventListener("click", (event) => {
  if (!event.isTrusted || event.button !== 0 || !navigator.userActivation.isActive || !(event.target instanceof Element)) return;
  const about = event.target.closest("[data-hz-about-dialog] a[href]");
  if (about instanceof HTMLAnchorElement && !event.defaultPrevented) {
    const repository = "https://github.com/helmut-hoffer-von-ankershoffen/heinzel";
    const release = `${repository}/releases/tag/v`;
    const action = about.href === repository ? "repository" : about.href.startsWith(release) ? "release" : null;
    if (action) {
      event.preventDefault();
      void ipcRenderer.invoke("hz:about-link", action, action === "release" ? about.href.slice(release.length) : null).catch(() => {});
      return;
    }
  }
  const anchor = event.target.closest("a[data-hz-public-media]");
  if (!(anchor instanceof HTMLAnchorElement)) return;
  event.preventDefault();
  const failed = () => anchor.dispatchEvent(new CustomEvent("hz-public-media-result", { detail: false }));
  void ipcRenderer.invoke("hz:public-media", anchor.href).then((opened) => { if (opened !== true) failed(); }, failed);
}, true);
