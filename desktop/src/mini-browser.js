import { createGuestNetwork } from "./mini-browser-network.js";
import { browserBounds, browserOwner, browserUrl, hasExactKeys } from "./mini-browser-policy.js";

/** The one persistent partition for websites: never the product's session, so no hz cookie or token is in it. */
export const BROWSER_PARTITION = "persist:hz-browser";
// Device APIs a site may ask for; the person then picks the device in a chooser. Camera, microphone, screen stay refused.
const DEVICE_PERMISSIONS = new Set(["hid", "serial", "usb"]);
const MAX_DEVICES = 8;
// A failed passkey ceremony reaches the page as a bare NotAllowedError; Electron serves no platform passkey here.
const PASSKEY_HINT = /NotAllowedError|PublicKeyCredential|webauthn|passkey/i;
const PASSKEY_PAGE = /\/challenge\/pk(?:\/|$)|passkey|webauthn/i;

/** Label and id of one device from any chooser event, or null when Electron sent no usable entry. */
function deviceChoice(device) {
  const id = device?.deviceId ?? device?.portId;
  if (typeof id !== "string" || !id) return null;
  const label = [device.productName ?? device.name ?? device.deviceName ?? device.displayName ?? device.portName,
    device.manufacturerName].filter((part) => typeof part === "string" && part.trim()).join(" · ");
  return { id, label: (label || id).slice(0, 80) };
}

/** Native message box options: one button per device, then Cancel; the response index is the device. */
export function deviceDialog({ host, devices }) {
  const site = typeof host === "string" && host ? host : "This website";
  return {
    type: "question",
    message: `${site} wants to connect to a device`,
    detail: "Choose the device to share with this website, or cancel. Only the device you choose is shared.",
    buttons: [...devices.map((device) => device.label), "Cancel"],
    cancelId: devices.length,
    defaultId: devices.length,
    noLink: true,
  };
}

/**
 * A guest has no product preload, session, headers, cookies or native permissions. All guests share one persistent
 * partition, so sign-ins survive closing the panel and restarting the app until the person clears browsing data.
 * `chooseDevice({ window, host, devices })` shows the person a native chooser and resolves to a device id or null.
 */
export function createMiniBrowser({ windows, fromWebContents, connection, createSession, createView,
  createNetwork = createGuestNetwork, chooseDevice = async () => null, openExternal = async () => {} }) {
  const guests = new Map();
  const opening = new Map();
  const generations = new WeakMap();
  let partition = null;
  let shared = null;
  let stopped = false;
  // The guest whose chooser this is: only a focused window shows its guest, and a site asks from a click in it.
  const promptOwner = () => [...guests].find(([window]) => !window.isDestroyed() && window.isFocused());
  const pick = async (list, notice) => {
    const owner = promptOwner();
    const devices = (Array.isArray(list) ? list : []).map(deviceChoice).filter(Boolean).slice(0, MAX_DEVICES);
    if (!owner) return null;
    const [window, guest] = owner;
    if (!devices.length) { guest.notice = notice; return null; }
    let host = "";
    try { host = new URL(guest.view.webContents.getURL()).host; } catch { /* no location yet */ }
    try {
      const id = await chooseDevice({ window, host, devices });
      return devices.some((device) => device.id === id) ? id : null;
    } catch { return null; }
  };
  // Electron cancels a HID/USB chooser with no device and a serial chooser with an empty port id.
  const chooser = (listOf, cancel) => (event, ...args) => {
    event.preventDefault();
    const callback = args.at(-1);
    if (typeof callback !== "function") return;
    void pick(listOf(args), "no-device").then((id) => (id ? callback(id) : cancel === undefined ? callback() : callback(cancel)));
  };
  const partitionFor = () => {
    if (partition) return partition;
    partition = createSession(BROWSER_PARTITION);
    partition.setPermissionCheckHandler((_contents, permission) => DEVICE_PERMISSIONS.has(permission));
    partition.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
    // No device permission handler: only a device the person picked in a chooser is granted.
    partition.setBluetoothPairingHandler?.((details, callback) => {
      if (details?.pairingKind !== "confirm" && details?.pairingKind !== "confirmPin") { callback({ confirmed: false }); return; }
      const label = details.pairingKind === "confirmPin" ? `Pair with PIN ${String(details.pin ?? "").slice(0, 16)}` : "Pair";
      void pick([{ deviceId: "pair", name: label }], "no-device").then((id) => callback({ confirmed: id === "pair" }));
    });
    partition.setDisplayMediaRequestHandler((_request, callback) => callback({}));
    partition.on("select-hid-device", chooser(([details]) => details?.deviceList));
    partition.on("select-usb-device", chooser(([details]) => details?.deviceList));
    partition.on("select-serial-port", chooser(([portList]) => portList, ""));
    partition.on("will-download", (event) => event.preventDefault());
    partition.webRequest.onBeforeRequest((details, callback) => {
      try { browserUrl(details.url, connection()?.origin); callback({ cancel: false }); }
      catch { callback({ cancel: true }); }
    });
    return partition;
  };
  // One CONNECT broker serves the shared partition while any guest exists; the last guest's close ends it.
  const networkFor = (session) => {
    shared ??= (async () => {
      const network = await createNetwork();
      try { await network.configure(session); } catch (error) { network.close(); throw error; }
      return network;
    })().catch((error) => { shared = null; throw error; });
    return shared;
  };
  const releaseNetwork = () => {
    if (guests.size || opening.size || !shared) return;
    const ending = shared;
    shared = null;
    void ending.then((network) => network.close(), () => {});
  };
  const close = (window) => {
    generations.set(window, (generations.get(window) ?? 0) + 1);
    const guest = guests.get(window);
    if (!guest) return;
    guests.delete(window);
    guest.detach();
    if (!window.isDestroyed()) window.contentView.removeChildView(guest.view);
    if (!guest.view.webContents.isDestroyed()) guest.view.webContents.close({ waitForBeforeUnload: false });
    // The partition keeps sign-ins; only "Clear browsing data" empties it.
    releaseNetwork();
  };
  // The partition persists across restarts, so clearing opens it even before the first guest of this run.
  const clear = async () => {
    const partition = partitionFor();
    await Promise.allSettled([partition.clearStorageData(), partition.clearCache?.(), partition.clearAuthCache?.()]);
    for (const guest of guests.values()) {
      guest.notice = null;
      if (guest.view.webContents.getURL()) guest.view.webContents.reload();
    }
  };
  const isNewDocument = (event) => event.isMainFrame && !event.isSameDocument;
  const open = async (window) => {
    const partition = partitionFor();
    let view, network;
    try {
      network = await networkFor(partition);
      if (stopped || window.isDestroyed()) throw new Error("Browser surface is closed.");
      view = createView({ webPreferences: {
        session: partition, sandbox: true, contextIsolation: true, nodeIntegration: false,
        nodeIntegrationInSubFrames: false, webviewTag: false, navigateOnDragDrop: false,
        safeDialogs: true, disableDialogs: true,
      } });
      const guest = { view, session: partition, network, status: "empty", notice: null };
      const contents = view.webContents;
      contents.setWebRTCIPHandlingPolicy("disable_non_proxied_udp");
      const refuseCredentials = (event, ...args) => {
        event.preventDefault();
        args.at(-1)();
      };
      contents.on("select-client-certificate", refuseCredentials);
      // Chromium repeats this event while it scans: ask once, with the devices found so far, answer the latest callback.
      let scan = null;
      contents.on("select-bluetooth-device", (event, devices, callback) => {
        event.preventDefault();
        scan ??= { asked: false, timer: null };
        const current = scan;
        current.callback = callback;
        const answer = (id) => { clearTimeout(current.timer); if (scan === current) scan = null; current.callback(id ?? ""); };
        if (current.asked) return;
        if (Array.isArray(devices) && devices.some(deviceChoice)) {
          current.asked = true;
          void pick(devices, "no-device").then(answer);
        } else current.timer ??= setTimeout(() => {
          if (current.asked) return;
          guest.notice = "no-device";
          answer("");
        }, 10_000);
      });
      // Electron has no platform passkey for third-party sites; the page only sees NotAllowedError. Say it may fail.
      contents.on("console-message", (details, ...legacy) => {
        const message = typeof details?.message === "string" ? details.message : legacy[1];
        if (typeof message === "string" && PASSKEY_HINT.test(message)) guest.notice = "passkey";
      });
      const passkeyPage = (_event, url) => {
        try { if (PASSKEY_PAGE.test(new URL(url).pathname)) guest.notice = "passkey"; } catch { /* not a URL */ }
      };
      contents.on("did-navigate", passkeyPage);
      contents.on("did-navigate-in-page", passkeyPage);
      contents.on("did-start-navigation", (details) => {
        if (isNewDocument(details)) guest.notice = null;
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
    notice: guest.notice,
  });
  return {
    async handle(event, request) {
      if (stopped) throw new Error("Browser surface is closed.");
      const window = browserOwner(event, { windows, fromWebContents, connection });
      if (!request || typeof request !== "object" || typeof request.action !== "string")
        throw new Error("Invalid browser command.");
      const fields = { navigate: ["action", "url"], layout: ["action", "bounds"], hide: ["action"], close: ["action"],
        state: ["action"], back: ["action"], forward: ["action"], reload: ["action"], external: ["action"], clear: ["action"] };
      if (!Object.hasOwn(fields, request.action) || !hasExactKeys(request, fields[request.action]))
        throw new Error("Invalid browser command.");
      if (request.action === "close") { close(window); return { status: "closed" }; }
      if (request.action === "clear") await clear();
      let guest = guests.get(window);
      if (request.action === "navigate") {
        const url = browserUrl(request.url, connection()?.origin);
        const generation = generations.get(window) ?? 0;
        if (!guest) {
          let navigated = false;
          const onNavigation = (details) => { if (isNewDocument(details)) navigated = true; };
          event.sender.on("did-start-navigation", onNavigation);
          try {
            if (stopped || navigated || (generations.get(window) ?? 0) !== generation)
              throw new Error("Browser surface is closed.");
            if (!guests.has(window)) {
              if (!opening.has(window)) opening.set(window, open(window).finally(() => { opening.delete(window); releaseNetwork(); }));
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
      // Only the guest's own current location, under the same public-HTTPS rule as any navigation.
      if (request.action === "external") await openExternal(browserUrl(guest.view.webContents.getURL(), connection()?.origin));
      return state(guest);
    },
    closeAll() { stopped = true; for (const window of [...guests.keys()]) close(window); },
  };
}
