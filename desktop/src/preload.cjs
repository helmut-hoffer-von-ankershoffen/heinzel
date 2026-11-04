const { contextBridge, ipcRenderer } = require("electron");

// A single fixed read operation; credential material never reaches the page.
contextBridge.exposeInMainWorld("heinzelDesktop", Object.freeze({
  lifecycle: () => ipcRenderer.invoke("hz:lifecycle"),
}));

// A trusted native click is required; renderer scripts receive no callable external-opening bridge.
document.addEventListener("click", (event) => {
  if (!event.isTrusted || event.button !== 0 || !navigator.userActivation.isActive || !(event.target instanceof Element)) return;
  const anchor = event.target.closest("a[data-hz-public-media]");
  if (!(anchor instanceof HTMLAnchorElement)) return;
  event.preventDefault();
  const failed = () => anchor.dispatchEvent(new CustomEvent("hz-public-media-result", { detail: false }));
  void ipcRenderer.invoke("hz:public-media", anchor.href).then((opened) => { if (opened !== true) failed(); }, failed);
}, true);
