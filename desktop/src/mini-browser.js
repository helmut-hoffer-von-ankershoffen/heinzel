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

/** A vendor or product id as Electron sends it: a number, or a decimal or 0x-hex string. */
function numeric(value) {
  if (typeof value === "number" && Number.isInteger(value)) return value;
  if (typeof value !== "string" || !value) return null;
  const parsed = /^0x[0-9a-f]+$/i.test(value) ? Number.parseInt(value, 16) : /^\d+$/.test(value) ? Number(value) : Number.NaN;
  return Number.isInteger(parsed) ? parsed : null;
}

/**
 * The identity the device permission handler can match. Electron 45 hands the chooser a list entry but the handler
 * its DeviceInfoToValue: HID {vendorId, productId, serialNumber|guid}, serial {vendor_id, product_id, serial_number},
 * USB {deviceId: guid}. Both shapes reduce to the same key; a device without a serial number has none (Electron keeps
 * it ephemeral, outside the handler).
 */
export function deviceIdentity(type, device) {
  if (!device || typeof device !== "object") return null;
  if (type === "usb") {
    const id = device.deviceId ?? device.guid;
    return typeof id === "string" && id ? `usb:${id}` : null;
  }
  if (type !== "hid" && type !== "serial") return null;
  const vendor = numeric(device.vendorId ?? device.vendor_id);
  const product = numeric(device.productId ?? device.product_id);
  const serial = device.serialNumber ?? device.serial_number ?? (type === "hid" ? device.guid : undefined);
  if (vendor === null || product === null || typeof serial !== "string" || !serial) return null;
  return `${type}:${vendor}:${product}:${serial}`;
}

/** Label and id of one device from any chooser event, or null when Electron sent no usable entry. */
function deviceChoice(device) {
  const id = device?.deviceId ?? device?.portId;
  if (typeof id !== "string" || !id) return null;
  const label = [device.productName ?? device.name ?? device.deviceName ?? device.displayName ?? device.portName,
    device.manufacturerName].filter((part) => typeof part === "string" && part.trim()).join(" · ");
  return { id, label: (label || id).slice(0, 80), device };
}

/** Native message box options: one button per device, then Cancel; the response index is the device. */
export function deviceDialog({ host, devices }) {
  const site = typeof host === "string" && host ? host : "This website";
  return {
    type: "question",
    message: `${site} wants to connect to a device`,
    detail: "Choose the device to share with this website, or cancel. Only the device you choose is shared.",
    // Prefixed, so a device that names itself "Cancel" or "Allow" cannot pass for a dialog choice.
    buttons: [...devices.map((device) => `Device: ${device.label}`), "Cancel"],
    cancelId: devices.length,
    defaultId: devices.length,
    noLink: true,
  };
}

/**
 * A guest has no product preload, session, headers, cookies or native permissions. All guests share one persistent
 * partition, so sign-ins survive closing the panel and restarting the app until the person clears browsing data.
 * `chooseDevice({ window, host, devices })` shows the person a native chooser and resolves to a device id or null.
 * `openExternal(url, window)` applies the product window's gesture check and rate limit and returns whether it opened.
 */
export function createMiniBrowser({ windows, fromWebContents, connection, createSession, createView,
  createNetwork = createGuestNetwork, chooseDevice = async () => null, openExternal = async () => false }) {
  const guests = new Map();
  const opening = new Map();
  const generations = new WeakMap();
  let partition = null;
  let shared = null;
  // Other guests on the website partition (the media player) hold a lease, so the broker outlives the panel.
  let leases = 0;
  let stopped = false;
  // Devices the person picked, per requesting origin and device type; "Clear browsing data" revokes them all.
  const grants = new Set();
  const grantKey = (origin, identity) => JSON.stringify([origin, identity]);
  const originOf = (value) => { try { return new URL(value).origin; } catch { return ""; } };
  // The guest a prompt came from: its own contents, or the main frame above the requesting frame. A background
  // window never prompts, since only a focused window shows its guest and a site asks from a click in it.
  const promptOwner = ({ contents, frame }) => [...guests].find(([window, guest]) => {
    const own = guest.view.webContents;
    const top = own.mainFrame;
    return !window.isDestroyed() && window.isFocused() &&
      (own === contents || (frame && top && (frame === top || frame.top === top)));
  });
  /** Ask the person for one device; resolves to the picked id, recording the grant for `type`, or null. */
  const pick = async (list, { type, contents, frame, notice = "no-device" }) => {
    const owner = promptOwner({ contents, frame });
    const devices = (Array.isArray(list) ? list : []).map(deviceChoice).filter(Boolean).slice(0, MAX_DEVICES);
    if (!owner) return null;
    const [window, guest] = owner;
    if (!devices.length) { guest.notice = notice; return null; }
    // Name the requester: an embed's own origin, not only the page around it.
    const origin = originOf(frame?.origin && frame.origin !== "null" ? frame.origin : frame?.url ?? guest.view.webContents.getURL());
    try {
      const offered = devices.map(({ id, label }) => ({ id, label }));
      const id = await chooseDevice({ window, host: origin ? new URL(origin).host : "", devices: offered });
      const chosen = devices.find((device) => device.id === id);
      if (!chosen) return null;
      const identity = deviceIdentity(type, chosen.device);
      if (identity && origin) grants.add(grantKey(origin, identity));
      return id;
    } catch { return null; }
  };
  // Electron cancels a HID/USB chooser with no device and a serial chooser with an empty port id.
  const chooser = (type, source, cancel) => (event, ...args) => {
    event.preventDefault();
    const callback = args.at(-1);
    if (typeof callback !== "function") return;
    const { list, ...from } = source(args);
    void pick(list, { type, ...from }).then((id) => (id ? callback(id) : cancel === undefined ? callback() : callback(cancel)));
  };
  const partitionFor = () => {
    if (partition) return partition;
    partition = createSession(BROWSER_PARTITION);
    partition.setPermissionCheckHandler((_contents, permission) => DEVICE_PERMISSIONS.has(permission));
    partition.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
    // Only a device the person picked for this origin is granted, until browsing data is cleared.
    partition.setDevicePermissionHandler((details) => {
      const identity = deviceIdentity(details?.deviceType, details?.device);
      return Boolean(identity) && grants.has(grantKey(originOf(details?.origin), identity));
    });
    partition.setBluetoothPairingHandler?.((details, callback) => {
      if (details?.pairingKind !== "confirm" && details?.pairingKind !== "confirmPin") { callback({ confirmed: false }); return; }
      const label = details.pairingKind === "confirmPin" ? `Pair with PIN ${String(details.pin ?? "").slice(0, 16)}` : "Pair";
      void pick([{ deviceId: "pair", name: label }], { frame: details.frame })
        .then((id) => callback({ confirmed: id === "pair" }));
    });
    partition.setDisplayMediaRequestHandler((_request, callback) => callback({}));
    partition.on("select-hid-device", chooser("hid", ([details]) => ({ list: details?.deviceList, frame: details?.frame })));
    partition.on("select-usb-device", chooser("usb", ([details]) => ({ list: details?.deviceList, frame: details?.frame })));
    partition.on("select-serial-port", chooser("serial", ([portList, contents]) => ({ list: portList, contents }), ""));
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
    if (guests.size || opening.size || leases || !shared) return;
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
  /**
   * The partition persists across restarts, so clearing opens it even before the first guest of this run. Pages are
   * taken off their sites first, so no running script writes cookies or storage back; then they load again.
   */
  const clear = async () => {
    const partition = partitionFor();
    // A guest still opening would load its site after the pages left theirs: wait for it first.
    await Promise.allSettled([...opening.values()]);
    grants.clear();
    const pages = [...guests.values()].map((guest) => [guest, guest.view.webContents.getURL()]);
    await Promise.allSettled(pages.map(([guest]) => guest.view.webContents.loadURL("about:blank")));
    const results = await Promise.allSettled([partition.clearStorageData(), partition.clearCache?.(),
      partition.clearAuthCache?.()]);
    const failed = results.some((result) => result.status === "rejected");
    for (const [guest, url] of pages) {
      // Kept apart from page notices, so restoring the page cannot reset it; the next good clear does.
      guest.clearFailed = failed;
      guest.notice = null;
      if (url && !guest.view.webContents.isDestroyed())
        void guest.view.webContents.loadURL(url).catch(() => { guest.status = "unavailable"; });
    }
    if (failed) throw new Error("Browsing data could not be cleared.");
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
          void pick(devices, { contents }).then(answer);
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
    notice: guest.clearFailed ? "clear-failed" : guest.notice,
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
      if (request.action === "external") await openExternal(browserUrl(guest.view.webContents.getURL(), connection()?.origin), window);
      return state(guest);
    },
    /** The website partition with its configured broker for another guest; `release()` once that guest closes. */
    async lease() {
      if (stopped) throw new Error("Browser surface is closed.");
      const session = partitionFor();
      leases += 1;
      let released = false;
      const release = () => { if (!released) { released = true; leases -= 1; releaseNetwork(); } };
      try {
        const network = await networkFor(session);
        return { session, network, release };
      } catch (error) { release(); throw error; }
    },
    closeAll() { stopped = true; for (const window of [...guests.keys()]) close(window); },
  };
}
