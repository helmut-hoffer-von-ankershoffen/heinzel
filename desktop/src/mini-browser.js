import { randomUUID } from "node:crypto";
import { createGuestNetwork } from "./mini-browser-network.js";
import { browserBounds, browserOwner, browserUrl, hasExactKeys } from "./mini-browser-policy.js";

/** A guest has no product preload, session, headers, cookies or native permissions. */
export function createMiniBrowser({ windows, fromWebContents, connection, createSession, createView, enabled, createNetwork = createGuestNetwork }) {
  const guests = new Map();
  const opening = new Map();
  const generations = new WeakMap();
  // One in-memory partition per product window, wiped on every close, so reopening never grows memory (Electron
  // cannot free a partition). The next guest waits for the wipe, so no state crosses from one browsing session.
  const partitions = new WeakMap();
  const wipes = new WeakMap();
  let stopped = false;
  // Electron cancels a HID/USB chooser with no device and a serial chooser with an empty port id.
  const refuseDevice = (cancel) => (event, ...args) => {
    event.preventDefault();
    const callback = args.at(-1);
    if (typeof callback === "function") cancel === undefined ? callback() : callback(cancel);
  };
  const partitionFor = (window) => {
    const existing = partitions.get(window);
    if (existing) return existing;
    const partition = createSession(`hz-browser-${randomUUID()}`, { cache: false });
    partition.setPermissionCheckHandler(() => false);
    partition.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
    // Device choosers would otherwise pick the first device; screen sharing gets no source.
    partition.setDevicePermissionHandler(() => false);
    partition.setBluetoothPairingHandler?.((_details, callback) => callback({ confirmed: false }));
    partition.setDisplayMediaRequestHandler((_request, callback) => callback({}));
    partition.on("select-hid-device", refuseDevice());
    partition.on("select-usb-device", refuseDevice());
    partition.on("select-serial-port", refuseDevice(""));
    partition.on("will-download", (event) => event.preventDefault());
    partition.webRequest.onBeforeRequest((details, callback) => {
      try { browserUrl(details.url, connection()?.origin); callback({ cancel: false }); }
      catch { callback({ cancel: true }); }
    });
    partitions.set(window, partition);
    return partition;
  };
  const close = (window) => {
    generations.set(window, (generations.get(window) ?? 0) + 1);
    const guest = guests.get(window);
    if (!guest) return;
    guests.delete(window);
    guest.detach();
    guest.network.close();
    if (!window.isDestroyed()) window.contentView.removeChildView(guest.view);
    if (!guest.view.webContents.isDestroyed()) guest.view.webContents.close({ waitForBeforeUnload: false });
    // A non-persistent partition is never shared with the product; closing wipes it before any reuse.
    wipes.set(window, Promise.allSettled([guest.session.clearStorageData(), guest.session.clearCache?.(),
      guest.session.clearAuthCache?.()]));
  };
  const isNewDocument = (event) => event.isMainFrame && !event.isSameDocument;
  const open = async (window) => {
    await wipes.get(window);
    const partition = partitionFor(window);
    const network = await createNetwork();
    let view;
    try {
      await network.configure(partition);
      if (stopped || window.isDestroyed()) { network.close(); throw new Error("Browser surface is closed."); }
      view = createView({ webPreferences: {
        session: partition, sandbox: true, contextIsolation: true, nodeIntegration: false,
        nodeIntegrationInSubFrames: false, webviewTag: false, navigateOnDragDrop: false,
        safeDialogs: true, disableDialogs: true,
      } });
      const guest = { view, session: partition, network, status: "empty" };
      const contents = view.webContents;
      contents.setWebRTCIPHandlingPolicy("disable_non_proxied_udp");
      const refuseCredentials = (event, ...args) => {
        event.preventDefault();
        args.at(-1)();
      };
      contents.on("select-client-certificate", refuseCredentials);
      contents.on("select-bluetooth-device", (event, _devices, callback) => {
        event.preventDefault();
        callback("");
      });
      contents.on("login", (event, _details, info, callback) => {
        event.preventDefault();
        network.authenticate(info, callback);
      });
      contents.on("will-prevent-unload", (event) => event.preventDefault());
      contents.setWindowOpenHandler(() => { guest.status = "unsupported"; return { action: "deny" }; });
      const guard = (event) => {
        try { browserUrl(event.url, connection()?.origin); }
        catch { event.preventDefault(); guest.status = "unsupported"; }
      };
      contents.on("will-navigate", guard);
      contents.on("will-frame-navigate", guard);
      contents.on("will-redirect", guard);
      contents.on("will-attach-webview", (event) => event.preventDefault());
      contents.on("did-start-loading", () => { guest.status = "loading"; });
      contents.on("did-stop-loading", () => { if (guest.status === "loading") guest.status = "ready"; });
      contents.on("did-fail-load", (_event, code, _description, _url, mainFrame) => {
        if (mainFrame && code !== -3) guest.status = "unavailable";
      });
      contents.on("render-process-gone", () => {
        guest.status = "unavailable";
        view.setVisible(false);
      });
      window.contentView.addChildView(view);
      view.setVisible(false);
      guests.set(window, guest);
      const onClosed = () => close(window);
      const onNavigation = (event) => {
        if (isNewDocument(event)) close(window);
      };
      const onBlur = () => view.setVisible(false);
      guest.detach = () => {
        window.off("closed", onClosed);
        window.off("blur", onBlur);
        window.webContents.off("did-start-navigation", onNavigation);
      };
      window.on("closed", onClosed);
      window.on("blur", onBlur);
      window.webContents.on("did-start-navigation", onNavigation);
      return guest;
    } catch {
      network.close();
      if (guests.has(window)) close(window);
      else if (view && !view.webContents.isDestroyed()) view.webContents.close({ waitForBeforeUnload: false });
      throw new Error("Browser network is unavailable.");
    }
  };
  const state = (guest) => ({
    status: guest.status,
    url: guest.view.webContents.getURL(),
    canGoBack: guest.view.webContents.navigationHistory.canGoBack(),
    canGoForward: guest.view.webContents.navigationHistory.canGoForward(),
  });
  return {
    async handle(event, request) {
      if (stopped) throw new Error("Browser surface is closed.");
      const window = browserOwner(event, { windows, fromWebContents, connection });
      if (!request || typeof request !== "object" || typeof request.action !== "string")
        throw new Error("Invalid browser command.");
      const fields = { navigate: ["action", "url"], layout: ["action", "bounds"], hide: ["action"], close: ["action"],
        state: ["action"], back: ["action"], forward: ["action"], reload: ["action"] };
      if (!Object.hasOwn(fields, request.action) || !hasExactKeys(request, fields[request.action]))
        throw new Error("Invalid browser command.");
      if (request.action === "close") { close(window); return { status: "closed" }; }
      let guest = guests.get(window);
      if (request.action === "navigate") {
        const url = browserUrl(request.url, connection()?.origin);
        const generation = generations.get(window) ?? 0;
        if (!guest) {
          let navigated = false;
          const onNavigation = (details) => { if (isNewDocument(details)) navigated = true; };
          event.sender.on("did-start-navigation", onNavigation);
          try {
            if (!(await enabled())) throw new Error("Browser panel is not enabled.");
            if (stopped || navigated || (generations.get(window) ?? 0) !== generation)
              throw new Error("Browser surface is closed.");
            if (!guests.has(window)) {
              if (!opening.has(window)) opening.set(window, open(window).finally(() => opening.delete(window)));
              guest = await opening.get(window);
            }
            if (navigated || stopped || (generations.get(window) ?? 0) !== generation) {
              close(window);
              throw new Error("Browser surface is closed.");
            }
          } finally {
            event.sender.off("did-start-navigation", onNavigation);
          }
          if (navigated) throw new Error("Browser surface is closed.");
        }
        if (stopped || (generations.get(window) ?? 0) !== generation) throw new Error("Browser surface is closed.");
        browserOwner(event, { windows, fromWebContents, connection });
        guest = guests.get(window);
        void guest.view.webContents.loadURL(url).catch(() => { guest.status = "unavailable"; });
      }
      if (!guest) return { status: "empty", url: "", canGoBack: false, canGoForward: false };
      if (request.action === "layout") {
        try {
          const zoom = event.sender.getZoomFactor();
          const size = window.getContentSize();
          const css = browserBounds(request.bounds, size.map((dimension) => dimension / zoom));
          const bounds = Object.fromEntries(Object.entries(css).map(([key, value]) => [key, Math.round(value * zoom)]));
          guest.view.setBounds(browserBounds(bounds, size));
        } catch (error) {
          guest.view.setVisible(false);
          throw error;
        }
        guest.view.setVisible(window.isFocused() && guest.status !== "unavailable");
      }
      if (request.action === "hide") guest.view.setVisible(false);
      const history = guest.view.webContents.navigationHistory;
      if (request.action === "back" && history.canGoBack()) history.goBack();
      if (request.action === "forward" && history.canGoForward()) history.goForward();
      if (request.action === "reload") guest.view.webContents.reload();
      return state(guest);
    },
    closeAll() { stopped = true; for (const window of [...guests.keys()]) close(window); },
  };
}
