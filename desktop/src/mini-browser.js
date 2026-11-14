import { randomBytes } from "node:crypto";
import { createGuestNetwork } from "./mini-browser-network.js";
import { builtInBrowserLink, classifyUiLink, createUserActivation, grantOnNativeInput } from "./ui-navigation.js";
import { browserBounds, browserOwner, browserUrl, hasExactKeys } from "./mini-browser-policy.js";

/** The one persistent partition for websites: never the product's session, so no hz cookie or token is in it. */
export const BROWSER_PARTITION = "persist:hz-browser";
// Device APIs a site may ask for; the person then picks the device in a chooser. Camera, microphone, screen stay refused.
const DEVICE_PERMISSIONS = new Set(["hid", "serial", "usb"]);
const MAX_DEVICES = 8;
/** The person's own tabs per window. Agent tabs (a later head) count against their own limits, tagged by `owner`. */
export const MAX_PERSON_TABS = 8;
const TAB_ID = /^t-[0-9a-f]{8}$/;
// The shape of each command; a command may also carry one optional key (a tab id, or a first address).
const FIELDS = { navigate: ["action", "url"], layout: ["action", "bounds"], hide: ["action"], close: ["action"],
  state: ["action"], back: ["action"], forward: ["action"], reload: ["action"], external: ["action"], clear: ["action"],
  tabs: ["action"], "new-tab": ["action"], "select-tab": ["action", "tab"], "close-tab": ["action", "tab"],
  "link-external": ["action"], "link-dismiss": ["action"] };
const OPTIONAL = { navigate: "tab", state: "tab", back: "tab", forward: "tab", reload: "tab", external: "tab", "new-tab": "url" };
// A failed passkey ceremony reaches the page as a bare NotAllowedError; Electron serves no platform passkey here.
const PASSKEY_HINT = /NotAllowedError|PublicKeyCredential|webauthn|passkey/i;
const PASSKEY_PAGE = /\/challenge\/pk(?:\/|$)|passkey|webauthn/i;
// A page's own icon, for its tab and for bookmarks: read once per origin, kept small, never from a third-party service.
const MAX_FAVICON_DOWNLOAD = 512 * 1024;
export const MAX_FAVICON_BYTES = 6000;
// 6000 bytes as base64, plus the data: prefix.
const MAX_FAVICON_DATA_URL = 8100;
const MAX_FAVICON_ORIGINS = 200;
const PNG_MAGIC = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);

/** The icon as a 32-pixel PNG `data:` URL through the app's image decoder, or null for what it cannot read. */
export function smallFavicon(buffer, decode) {
  try {
    const image = decode(buffer);
    if (!image || image.isEmpty()) return smallPng(buffer);
    const { width, height } = image.getSize();
    const small = width > 32 || height > 32 ? image.resize({ width: 32, height: 32, quality: "best" }) : image;
    return smallPng(small.toPNG());
  } catch { return null; }
}

/** Without an image library only a PNG that is small already is kept; the app passes a resizing `faviconImage`. */
export function smallPng(buffer) {
  if (!Buffer.isBuffer(buffer) || buffer.length > MAX_FAVICON_BYTES || !buffer.subarray(0, 8).equals(PNG_MAGIC)) return null;
  return `data:image/png;base64,${buffer.toString("base64")}`;
}

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
  createNetwork = createGuestNetwork, chooseDevice = async () => null, openExternal = async () => false,
  faviconImage = smallPng }) {
  // window -> { tabs: Map(id -> guest), selected, bounds, shown, opening, pending }: every tab shares one partition.
  const surfaces = new Map();
  const opening = new Set();
  const generations = new WeakMap();
  let partition = null;
  let shared = null;
  let stopped = false;
  // Devices the person picked, per requesting origin and device type; "Clear browsing data" revokes them all.
  const grants = new Set();
  const grantKey = (origin, identity) => JSON.stringify([origin, identity]);
  const originOf = (value) => { try { return new URL(value).origin; } catch { return ""; } };
  // origin -> data: URL of the icon a page of that origin showed; emptied with the browsing data.
  const icons = new Map();
  /**
   * The first icon the page names that is public HTTPS, read in the guests' own partition (same proxy, same rules),
   * bounded and reduced to a small image. A page that left for another origin meanwhile keeps no stale icon.
   */
  const readFavicon = async (guest, urls) => {
    const page = originOf(guest.view.webContents.getURL());
    if (!page.startsWith("https://") || !Array.isArray(urls)) return;
    for (const candidate of urls.slice(0, 4)) {
      let url;
      try { url = browserUrl(candidate, connection()?.origin); } catch { continue; }
      try {
        const response = await guest.session.fetch(url, { credentials: "omit" });
        const type = String(response.headers.get("content-type") ?? "").toLowerCase();
        const length = Number(response.headers.get("content-length") ?? 0);
        if (!response.ok || !type.startsWith("image/") || length > MAX_FAVICON_DOWNLOAD) continue;
        const buffer = Buffer.from(await response.arrayBuffer());
        if (!buffer.length || buffer.length > MAX_FAVICON_DOWNLOAD) continue;
        const image = faviconImage(buffer);
        if (typeof image !== "string" || !image.startsWith("data:image/") || image.length > MAX_FAVICON_DATA_URL) continue;
        if (icons.size >= MAX_FAVICON_ORIGINS && !icons.has(page)) icons.delete(icons.keys().next().value);
        icons.set(page, image);
        return;
      } catch { /* the next candidate */ }
    }
  };
  // The guest a prompt came from: its own contents, or the main frame above the requesting frame. A background
  // window never prompts, since only a focused window shows its guest and a site asks from a click in it.
  const everyGuest = () => [...surfaces].flatMap(([window, surface]) =>
    [...surface.tabs.values()].map((guest) => [window, guest, surface]));
  const tabCount = () => everyGuest().length;
  // Only the selected tab may prompt: a background tab asking for a device is not something the person is looking at.
  const promptOwner = ({ contents, frame }) => everyGuest().find(([window, guest, surface]) => {
    const own = guest.view.webContents;
    const top = own.mainFrame;
    return !window.isDestroyed() && window.isFocused() && surface.selected === guest.id &&
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
  const isNewDocument = (event) => event.isMainFrame && !event.isSameDocument;
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
    if (tabCount() || opening.size || !shared) return;
    const ending = shared;
    shared = null;
    void ending.then((network) => network.close(), () => {});
  };
  const apply = (window, surface) => {
    for (const guest of surface.tabs.values()) {
      const visible = surface.shown && surface.selected === guest.id && !guest.blank && window.isFocused() &&
        guest.status !== "unavailable";
      if (!guest.view.webContents.isDestroyed()) guest.view.setVisible(Boolean(visible));
    }
  };
  // A fresh id that no tab of this window holds, retried on a collision.
  const newTabId = (window) => {
    for (;;) {
      const id = `t-${randomBytes(4).toString("hex")}`;
      if (!surfaces.get(window)?.tabs.has(id)) return id;
    }
  };
  const surfaceOf = (window) => {
    let surface = surfaces.get(window);
    if (surface) return surface;
    surface = { tabs: new Map(), selected: null, bounds: null, shown: false, opening: null, pending: 0, link: null };
    const onClosed = () => close(window);
    const onNavigation = (event) => { if (isNewDocument(event)) close(window); };
    const onBlur = () => { for (const guest of surface.tabs.values()) guest.view.setVisible(false); };
    surface.detach = () => {
      window.off("closed", onClosed);
      window.off("blur", onBlur);
      window.webContents.off("did-start-navigation", onNavigation);
    };
    window.on("closed", onClosed);
    window.on("blur", onBlur);
    window.webContents.on("did-start-navigation", onNavigation);
    surfaces.set(window, surface);
    return surface;
  };
  const dispose = (window, guest) => {
    if (!window.isDestroyed()) window.contentView.removeChildView(guest.view);
    if (!guest.view.webContents.isDestroyed()) guest.view.webContents.close({ waitForBeforeUnload: false });
  };
  const closeTab = (window, id) => {
    const surface = surfaces.get(window);
    const guest = surface?.tabs.get(id);
    if (!guest) return;
    const ids = [...surface.tabs.keys()];
    surface.tabs.delete(id);
    if (surface.selected === id) {
      const next = ids[ids.indexOf(id) + 1] ?? ids[ids.indexOf(id) - 1] ?? null;
      surface.selected = next;
    }
    dispose(window, guest);
    apply(window, surface);
    releaseNetwork();
  };
  function close(window) {
    generations.set(window, (generations.get(window) ?? 0) + 1);
    const surface = surfaces.get(window);
    if (!surface) return;
    surfaces.delete(window);
    surface.detach();
    for (const guest of surface.tabs.values()) dispose(window, guest);
    // The partition keeps sign-ins; only "Clear browsing data" empties it.
    releaseNetwork();
  }
  /**
   * The partition persists across restarts, so clearing opens it even before the first guest of this run. Pages are
   * taken off their sites first, so no running script writes cookies or storage back; then they load again.
   */
  const clear = async () => {
    const partition = partitionFor();
    // A guest still opening would load its site after the pages left theirs: wait for it first.
    await Promise.allSettled([...opening.values()]);
    grants.clear();
    icons.clear();
    const pages = everyGuest().map(([, guest]) => guest).map((guest) => [guest, guest.view.webContents.getURL()]);
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
      const guest = { id: newTabId(window), owner: "person", blank: true, view, session: partition,
        network, status: "empty", notice: null };
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
      contents.on("page-favicon-updated", (_event, urls) => { void readFavicon(guest, urls).catch(() => {}); });
      // target=_blank and window.open in a page open a new tab, but only right after the person's own input in this
      // page, and not faster than one a half second: Electron's window-open details carry no gesture.
      const activation = createUserActivation();
      grantOnNativeInput(contents, activation);
      let lastPopup = Number.NEGATIVE_INFINITY;
      contents.setWindowOpenHandler((details) => {
        const target = builtInBrowserLink(details?.url, connection()?.origin);
        const time = Date.now();
        if (!target || !activation.consume() || time - lastPopup < 500) { guest.status = "unsupported"; return { action: "deny" }; }
        lastPopup = time;
        void openLink(window, target).catch(() => {});
        return { action: "deny" };
      });
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
      const surface = surfaceOf(window);
      surface.tabs.set(guest.id, guest);
      surface.selected = guest.id;
      apply(window, surface);
      return guest;
    } catch {
      const registered = [...(surfaces.get(window)?.tabs.values() ?? [])].find((guest) => guest.view === view);
      if (registered) closeTab(window, registered.id);
      else if (view && !view.webContents.isDestroyed()) view.webContents.close({ waitForBeforeUnload: false });
      throw new Error("Browser network is unavailable.");
    }
  };
  const tabInfo = (guest, selected) => {
    const url = guest.view.webContents.getURL();
    return { id: guest.id, owner: guest.owner, selected, status: guest.status, url,
      title: String(guest.view.webContents.getTitle?.() ?? "").slice(0, 200), favicon: icons.get(originOf(url)) ?? null };
  };
  // The selected tab's state, plus the whole strip.
  const state = (window, target) => {
    const surface = surfaces.get(window);
    const tabs = [...(surface?.tabs.values() ?? [])].map((guest) => tabInfo(guest, guest.id === surface.selected));
    const guest = target ?? surface?.tabs.get(surface.selected);
    const link = surface?.link ?? null;
    if (!guest) return { status: "empty", url: "", canGoBack: false, canGoForward: false, notice: null, tabs, selected: null, link };
    return {
      status: guest.status,
      url: guest.view.webContents.getURL(),
      canGoBack: guest.view.webContents.navigationHistory.canGoBack(),
      canGoForward: guest.view.webContents.navigationHistory.canGoForward(),
      notice: guest.clearFailed ? "clear-failed" : guest.notice,
      tabs,
      selected: surface?.selected ?? null,
      link,
    };
  };
  /** A new guest for this window, or the one already opening when a first page is asked twice. */
  const create = async (event, window, { reuse = false } = {}) => {
    const surface = surfaceOf(window);
    if (reuse && surface.opening) return surface.opening;
    const generation = generations.get(window) ?? 0;
    let navigated = false;
    const onNavigation = (details) => { if (isNewDocument(details)) navigated = true; };
    event.sender.on("did-start-navigation", onNavigation);
    try {
      const changed = () => navigated || stopped || (generations.get(window) ?? 0) !== generation;
      if (changed()) throw new Error("Browser surface is closed.");
      const mine = [...surface.tabs.values()].filter((tab) => tab.owner === "person").length;
      if (mine + surface.pending >= MAX_PERSON_TABS) throw new Error("Too many tabs.");
      surface.pending += 1;
      const pending = open(window).finally(() => { surface.pending -= 1; opening.delete(pending); if (surface.opening === pending) surface.opening = null; releaseNetwork(); });
      opening.add(pending);
      if (reuse) surface.opening = pending;
      const guest = await pending;
      if (changed()) {
        close(window);
        throw new Error("Browser surface is closed.");
      }
      return guest;
    } finally {
      event.sender.off("did-start-navigation", onNavigation);
    }
  };
  /**
   * A link from the product UI, an embed or a page: a new tab on the selected tab, under every rule of a typed address.
   * A refused address or a full strip leaves a note with the link, which the person may open in the default browser.
   */
  async function openLink(window, target) {
    if (stopped || window.isDestroyed()) return state(window);
    const surface = surfaceOf(window);
    const external = classifyUiLink(target, connection()).kind === "external";
    let url;
    try { url = browserUrl(target, connection()?.origin); }
    catch { surface.link = { url: target.slice(0, 2048), reason: "refused", external }; return state(window); }
    const mine = [...surface.tabs.values()].filter((tab) => tab.owner === "person").length;
    if (mine + surface.pending >= MAX_PERSON_TABS) {
      surface.link = { url, reason: "limit", external };
      return state(window);
    }
    const guest = await create({ sender: window.webContents }, window);
    surfaceOf(window).link = null;
    guest.blank = false;
    void guest.view.webContents.loadURL(url).catch(() => { guest.status = "unavailable"; });
    return state(window);
  }
  const tabOf = (surface, id) => {
    if (typeof id !== "string" || !TAB_ID.test(id) || !surface?.tabs.has(id)) throw new Error("Unknown tab.");
    return surface.tabs.get(id);
  };
  return {
    async handle(event, request) {
      if (stopped) throw new Error("Browser surface is closed.");
      const window = browserOwner(event, { windows, fromWebContents, connection });
      if (!request || typeof request !== "object" || typeof request.action !== "string")
        throw new Error("Invalid browser command.");
      const { action } = request;
      const keys = Object.hasOwn(FIELDS, action) ? FIELDS[action] : null;
      const extra = OPTIONAL[action];
      if (!keys || !(hasExactKeys(request, keys) || (extra && hasExactKeys(request, [...keys, extra]))))
        throw new Error("Invalid browser command.");
      if (action === "close") { close(window); return { status: "closed" }; }
      if (action === "clear") await clear();
      let surface = surfaces.get(window);
      if (action === "link-dismiss") { if (surface) surface.link = null; return state(window); }
      if (action === "link-external") {
        const link = surface?.link;
        if (link?.external) {
          surface.link = null;
          await openExternal(link.url, window);
        }
        return state(window);
      }
      if (action === "close-tab") { tabOf(surface, request.tab); closeTab(window, request.tab); return state(window); }
      if (action === "select-tab") {
        tabOf(surface, request.tab);
        surface.selected = request.tab;
        apply(window, surface);
        return state(window);
      }
      let guest = request.tab === undefined ? surface?.tabs.get(surface.selected) : tabOf(surface, request.tab);
      const generation = generations.get(window) ?? 0;
      if (action === "new-tab" || action === "navigate") {
        // Check the address before a tab is made for it.
        const url = action === "navigate" ? browserUrl(request.url, connection()?.origin)
          : request.url === undefined ? null : browserUrl(request.url, connection()?.origin);
        if (action === "new-tab" || !guest) guest = await create(event, window, { reuse: action === "navigate" });
        if (stopped || (generations.get(window) ?? 0) !== generation) throw new Error("Browser surface is closed.");
        browserOwner(event, { windows, fromWebContents, connection });
        surface = surfaces.get(window);
        if (url) {
          guest.blank = false;
          void guest.view.webContents.loadURL(url).catch(() => { guest.status = "unavailable"; });
        }
        if (action === "new-tab") return state(window);
      }
      if (!guest) return state(window);
      if (action === "layout") {
        try {
          const zoom = event.sender.getZoomFactor();
          const size = window.getContentSize();
          const css = browserBounds(request.bounds, size.map((dimension) => dimension / zoom));
          const bounds = Object.fromEntries(Object.entries(css).map(([key, value]) => [key, Math.round(value * zoom)]));
          surface.bounds = browserBounds(bounds, size);
          for (const tab of surface.tabs.values()) tab.view.setBounds(surface.bounds);
        } catch (error) {
          surface.shown = false;
          apply(window, surface);
          throw error;
        }
        surface.shown = true;
        apply(window, surface);
      }
      if (action === "hide") { surface.shown = false; apply(window, surface); }
      const history = guest.view.webContents.navigationHistory;
      if (action === "back" && history.canGoBack()) history.goBack();
      if (action === "forward" && history.canGoForward()) history.goForward();
      if (action === "reload") guest.view.webContents.reload();
      // Only the guest's own current location, under the same public-HTTPS rule as any navigation.
      if (action === "external") await openExternal(browserUrl(guest.view.webContents.getURL(), connection()?.origin), window);
      return state(window, guest);
    },
    openLink,
    closeAll() { stopped = true; for (const window of [...surfaces.keys()]) close(window); },
  };
}
