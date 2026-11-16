import { randomBytes } from "node:crypto";
import { createGuestNetwork } from "./mini-browser-network.js";
import { builtInBrowserLink, classifyUiLink, createUserActivation, grantOnNativeInput } from "./ui-navigation.js";
import { browserBounds, browserOwner, browserUrl, hasExactKeys } from "./mini-browser-policy.js";

/** The one persistent partition for websites: never the product's session, so no hz cookie or token is in it. */
export const BROWSER_PARTITION = "persist:hz-browser";
// Device APIs a site may ask for; the person then picks the device in a chooser. Camera, microphone, screen stay refused.
const DEVICE_PERMISSIONS = new Set(["hid", "serial", "usb"]);
const MAX_DEVICES = 8;
/** The person's own tabs per window. Agent tabs count against their own limits, tagged by `owner`. */
export const MAX_PERSON_TABS = 8;
/** ADR 0162: 4 tabs per agent and 16 tabs in all per window. Agents share what the person's 8 leave, so the person is never refused a tab. */
export const MAX_AGENT_TABS = 4;
export const MAX_WINDOW_TABS = 16;
/** An agent tab closes after this long without a call from its agent. */
export const AGENT_IDLE_MS = 30 * 60 * 1000;
const AGENT_ID = /^[A-Za-z0-9_.:-]{1,80}$/;
const AGENT_KINDS = new Set(["heinzel", "external"]);
// Until the panel was laid out once, an agent tab still needs a size to render and to be captured.
const DEFAULT_BOUNDS = Object.freeze({ x: 0, y: 0, width: 1280, height: 800 });
/** The owner tag of an agent's tabs; never equal to "person", whatever the agent calls itself. */
export const agentOwner = (id) => `agent:${id}`;
const TAB_ID = /^t-[0-9a-f]{8}$/;
// The person's input that takes an agent's tab over: a press, a tap or a key down. Moving the pointer and the wheel
// only look at the page.
const PERSON_INPUT = { mouseDown: "mouseDown", touchStart: "touchStart", gestureTap: "gestureTap", keyDown: "keyDown", rawKeyDown: "keyDown" };
// Electron reports one input on more than one of these; the agent engine's own input passes them too.
const INPUT_CHANNELS = ["before-input-event", "input-event", "before-mouse-event"];
/** How long one input of the agent engine may take to show up as an event; after that any event is the person's. */
export const AGENT_INPUT_MS = 1000;
// The shape of each command; a command may also carry one optional key (a tab id, or a first address).
const FIELDS = { navigate: ["action", "url"], layout: ["action", "bounds"], hide: ["action"], close: ["action"],
  state: ["action"], diagnostics: ["action"], back: ["action"], forward: ["action"], reload: ["action"], external: ["action"], clear: ["action"],
  tabs: ["action"], "new-tab": ["action"], "select-tab": ["action", "tab"], "close-tab": ["action", "tab"],
  "link-external": ["action"], "link-dismiss": ["action"],
  "take-over": ["action", "tab"], "hand-back": ["action", "tab"], "stop-agent": ["action", "tab"],
  // An answer names the card the person read (`commit`): it never reaches another card on the same tab.
  "commit-approve": ["action", "tab", "commit"], "commit-refuse": ["action", "tab", "commit"] };
const OPTIONAL = { navigate: "tab", state: "tab", back: "tab", forward: "tab", reload: "tab", external: "tab", "new-tab": "url" };
// A failed passkey ceremony reaches the page as a bare NotAllowedError; Electron serves no platform passkey here.
const PASSKEY_HINT = /NotAllowedError|PublicKeyCredential|webauthn|passkey/i;
const PASSKEY_PAGE = /\/challenge\/pk(?:\/|$)|passkey|webauthn/i;
// A page's own icon, for its tab and for bookmarks: read once per origin, kept small, never from a third-party service.
export const MAX_FAVICON_DOWNLOAD = 512 * 1024;
const FAVICON_TIMEOUT_MS = 5000;
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
  faviconImage = smallPng, faviconTimeout = FAVICON_TIMEOUT_MS, now = () => Date.now(), platform = process.platform }) {
  // A website session fetches nothing on its own (ADR 0140). Chromium's own spell checker downloads its dictionaries
  // from a server; the macOS one is the system's and downloads nothing. So websites get spell check on macOS only.
  const spellcheck = platform === "darwin";
  // window -> { tabs: Map(id -> guest), selected, bounds, shown, opening, pending }: every tab shares one partition.
  const surfaces = new Map();
  const opening = new Set();
  const generations = new WeakMap();
  let partition = null;
  let shared = null;
  // Other guests on the website partition (the media player) hold a lease, so the broker outlives the panel.
  let leases = 0;
  // Pages of leased guests: clear takes them off their sites too, then lets each restore itself.
  const leasedPages = new Set();
  let stopped = false;
  // Devices the person picked, per requesting origin and device type; "Clear browsing data" revokes them all.
  const grants = new Set();
  // Agents the person stopped: their calls are refused until the daemon reports the person allowed them again.
  const stoppedAgents = new Set();
  const agentPending = new Map();
  const agentListeners = new Set();
  const tell = (event, guest) => {
    for (const listener of agentListeners) {
      try { listener({ event, agent: guest.agent.id, tab: event === "stop" ? null : guest.id }); } catch { /* a listener never breaks a tab */ }
    }
  };
  const grantKey = (origin, identity) => JSON.stringify([origin, identity]);
  const originOf = (value) => { try { return new URL(value).origin; } catch { return ""; } };
  // origin -> data: URL of the icon a page of that origin showed; emptied with the browsing data.
  const icons = new Map();
  /**
   * The first icon the page names that is public HTTPS, read in the guests' own partition (same proxy, same rules),
   * bounded and reduced to a small image. A page that left for another origin meanwhile keeps no stale icon.
   */
  // The body in pieces, never more than the cap in memory: past it the download is cancelled, whatever the headers said.
  const readCapped = async (response, abort) => {
    const reader = response.body?.getReader();
    if (!reader) return null;
    const chunks = [];
    let size = 0;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) return Buffer.concat(chunks);
      size += value.byteLength;
      if (size > MAX_FAVICON_DOWNLOAD) {
        abort.abort();
        await reader.cancel().catch(() => {});
        return null;
      }
      chunks.push(Buffer.from(value));
    }
  };
  const readFavicon = async (guest, urls) => {
    const page = originOf(guest.view.webContents.getURL());
    if (!page.startsWith("https://") || !Array.isArray(urls)) return;
    for (const candidate of urls.slice(0, 4)) {
      let url;
      try { url = browserUrl(candidate, connection()?.origin); } catch { continue; }
      // One deadline for the answer and its body: a site that never finishes costs a few seconds, not a connection.
      const abort = new AbortController();
      const deadline = setTimeout(() => abort.abort(), faviconTimeout);
      try {
        const response = await guest.session.fetch(url, { credentials: "omit", signal: abort.signal });
        const type = String(response.headers.get("content-type") ?? "").toLowerCase();
        const length = Number(response.headers.get("content-length") ?? 0);
        if (!response.ok || !type.startsWith("image/") || length > MAX_FAVICON_DOWNLOAD) { abort.abort(); continue; }
        const buffer = await readCapped(response, abort);
        if (!buffer?.length) continue;
        const image = faviconImage(buffer);
        if (typeof image !== "string" || !image.startsWith("data:image/") || image.length > MAX_FAVICON_DATA_URL) continue;
        if (icons.size >= MAX_FAVICON_ORIGINS && !icons.has(page)) icons.delete(icons.keys().next().value);
        icons.set(page, image);
        return;
      } catch { /* the next candidate */ }
      finally { clearTimeout(deadline); }
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
    // For every guest of the partition, the media player's included: no dictionary is asked for or fetched.
    if (!spellcheck) { partition.setSpellCheckerEnabled?.(false); partition.setSpellCheckerLanguages?.([]); }
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
    if (tabCount() || opening.size || leases || !shared) return;
    const ending = shared;
    shared = null;
    void ending.then((network) => network.close(), () => {});
  };
  const apply = (window, surface) => {
    for (const guest of surface.tabs.values()) {
      const visible = surface.shown && surface.selected === guest.id && !guest.blank && window.isFocused() &&
        guest.status !== "unavailable";
      if (!guest.view.webContents.isDestroyed()) guest.view.setVisible(Boolean(visible));
      // The agent engine gives a hidden tab a viewport and takes it away the moment the tab is shown.
      const shown = Boolean(visible);
      if (guest.agent && guest.shown !== shown) { guest.shown = shown; try { guest.onShown?.(shown); } catch { /* never breaks a tab */ } }
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
    surface = { tabs: new Map(), selected: null, bounds: null, shown: false, opening: null, pending: 0, pendingAgents: 0, link: null };
    const onClosed = () => close(window);
    const onNavigation = (event) => { if (isNewDocument(event)) close(window); };
    const onBlur = () => { for (const guest of surface.tabs.values()) { guest.view.setVisible(false); if (guest.agent) guest.shown = false; } };
    // Kept from the start: a destroyed window no longer hands out its webContents ("Object has been destroyed"),
    // and taking a listener off needs only the object it was put on.
    const contents = window.webContents;
    surface.detach = () => {
      for (const [emitter, name, listener] of [[window, "closed", onClosed], [window, "blur", onBlur], [contents, "did-start-navigation", onNavigation]]) {
        try { emitter.off(name, listener); } catch { /* gone, and its listeners with it */ }
      }
    };
    window.on("closed", onClosed);
    window.on("blur", onBlur);
    contents.on("did-start-navigation", onNavigation);
    surfaces.set(window, surface);
    return surface;
  };
  const dispose = (window, guest) => {
    clearTimeout(guest.idle);
    guest.commit?.answer(false);
    if (guest.agent) {
      // The debugger is only ever attached to an agent tab, and leaves with it.
      try { if (guest.view.webContents.debugger?.isAttached?.()) guest.view.webContents.debugger.detach(); } catch { /* gone */ }
      tell("closed", guest);
    }
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
    // The window may be destroyed already (it closes before the app quits): each tab is still let go, and one that
    // cannot be does not keep the others or the network open.
    for (const guest of surface.tabs.values()) {
      try { dispose(window, guest); } catch { /* destroyed with its window */ }
    }
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
    const others = [...leasedPages];
    const blanked = await Promise.allSettled([...pages.map(async ([guest]) => guest.view.webContents.loadURL("about:blank")),
      ...others.map(async (page) => page.blank())]);
    const results = await Promise.allSettled([partition.clearStorageData(), partition.clearCache?.(),
      partition.clearAuthCache?.(), ...pages.map(async ([guest]) => {
        if (!guest.view.webContents.isDestroyed()) guest.view.webContents.navigationHistory.clear();
      })]);
    const failed = [...blanked, ...results].some((result) => result.status === "rejected");
    for (const [guest, url] of pages) {
      // Kept apart from page notices, so restoring the page cannot reset it; the next good clear does.
      guest.clearFailed = failed;
      guest.notice = null;
      if (url && !guest.view.webContents.isDestroyed())
        void guest.view.webContents.loadURL(url).catch(() => { guest.status = "unavailable"; });
    }
    for (const page of others) if (leasedPages.has(page)) page.restore();
    if (failed) throw new Error("Browsing data could not be cleared.");
  };
  const open = async (window, agent = null) => {
    const partition = partitionFor();
    let view, network;
    try {
      network = await networkFor(partition);
      if (stopped || window.isDestroyed()) throw new Error("Browser surface is closed.");
      view = createView({ webPreferences: {
        session: partition, sandbox: true, contextIsolation: true, nodeIntegration: false,
        nodeIntegrationInSubFrames: false, webviewTag: false, navigateOnDragDrop: false,
        safeDialogs: true, disableDialogs: true, spellcheck,
      } });
      const guest = { id: newTabId(window), owner: agent ? agentOwner(agent.id) : "person", agent, paused: false, shown: false, expected: [],
        blank: true, view, session: partition, network, status: "empty", notice: null };
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
        // An agent's page opens nothing: popups stay refused for agent tabs (ADR 0162), whoever clicked.
        if (guest.agent) { guest.status = "unsupported"; return { action: "deny" }; }
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
      if (agent) {
        // The person's own key or click inside an agent's tab takes it over (ADR 0162), as the Take over button does.
        // The agent engine's own input passes the same events. It announces each one (`agents.expect`): one event of
        // that kind per channel is its own, for a second at most. Every other event is the person's, also one that
        // arrives while a call of the engine is still running.
        const own = (channel) => (_event, input) => {
          const kind = Object.hasOwn(PERSON_INPUT, input?.type ?? "") ? PERSON_INPUT[input.type] : null;
          if (!kind) return;
          const time = now();
          guest.expected = guest.expected.filter((entry) => entry.until > time);
          const index = guest.expected.findIndex((entry) => entry.channel === channel && entry.kind === kind);
          if (index >= 0) { guest.expected.splice(index, 1); return; }
          if (guest.paused) return;
          guest.paused = true;
          tell("take-over", guest);
        };
        for (const name of INPUT_CHANNELS) contents.on(name, own(name));
      }
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
      // An agent's new tab never takes the person's view; the person selects it to watch.
      if (agent) view.setBounds(surface.bounds ?? DEFAULT_BOUNDS);
      else surface.selected = guest.id;
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
      agent: guest.agent ? { name: guest.agent.name, kind: guest.agent.kind } : null, paused: guest.paused,
      commit: guest.commit ? { summary: guest.commit.summary, id: guest.commit.id } : null,
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
  const create = async (event, window, { reuse = false, agent = null } = {}) => {
    const surface = surfaceOf(window);
    if (reuse && surface.opening) return surface.opening;
    const generation = generations.get(window) ?? 0;
    let navigated = false;
    const onNavigation = (details) => { if (isNewDocument(details)) navigated = true; };
    event.sender.on("did-start-navigation", onNavigation);
    try {
      const changed = () => navigated || stopped || (generations.get(window) ?? 0) !== generation;
      if (changed()) throw new Error("Browser surface is closed.");
      const tabs = [...surface.tabs.values()];
      const mine = tabs.filter((tab) => tab.owner === "person").length;
      if (agent) {
        const owner = agentOwner(agent.id);
        const theirs = everyGuest().filter(([, tab]) => tab.owner === owner).length + (agentPending.get(agent.id) ?? 0);
        const agents = tabs.length - mine + surface.pendingAgents;
        if (theirs >= MAX_AGENT_TABS || agents >= MAX_WINDOW_TABS - MAX_PERSON_TABS) throw new Error("Too many tabs.");
        surface.pendingAgents += 1;
        agentPending.set(agent.id, (agentPending.get(agent.id) ?? 0) + 1);
      } else {
        if (mine + surface.pending >= MAX_PERSON_TABS) throw new Error("Too many tabs.");
        surface.pending += 1;
      }
      const pending = open(window, agent).finally(() => {
        if (agent) { surface.pendingAgents -= 1; agentPending.set(agent.id, agentPending.get(agent.id) - 1); }
        else surface.pending -= 1;
        opening.delete(pending); if (surface.opening === pending) surface.opening = null; releaseNetwork();
      });
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
  const closeAgent = (id) => {
    const owner = agentOwner(id);
    for (const [window, guest] of everyGuest()) if (guest.owner === owner) closeTab(window, guest.id);
  };
  const touch = (guest) => {
    clearTimeout(guest.idle);
    const window = everyGuest().find(([, tab]) => tab === guest)?.[0];
    if (!window) return;
    guest.idle = setTimeout(() => closeTab(window, guest.id), AGENT_IDLE_MS);
    guest.idle.unref?.();
  };
  const validAgent = (agent) => {
    if (!agent || typeof agent !== "object" || !hasExactKeys(agent, ["id", "kind", "name"]) ||
        typeof agent.id !== "string" || !AGENT_ID.test(agent.id) || !AGENT_KINDS.has(agent.kind) ||
        typeof agent.name !== "string" || !agent.name.trim() || agent.name.length > 60 || /[\u0000-\u001f\u007f]/u.test(agent.name))
      throw new Error("Invalid agent.");
    return Object.freeze({ id: agent.id, name: agent.name.trim(), kind: agent.kind });
  };
  /**
   * What the bridge's agent engine may do (ADR 0162). It names tabs by agent and tab id only and gets guests of this
   * table: never a window's own webContents, never the person's tab and never another agent's.
   */
  const agents = {
    /** The checked, frozen `{id, name, kind}` of an agent, or an error. */
    identify: validAgent,
    stopped: (id) => stoppedAgents.has(id),
    allow: (id) => { stoppedAgents.delete(id); },
    list: (id) => everyGuest().filter(([, guest]) => guest.owner === agentOwner(id)).map(([, guest]) => guest),
    /** The agent's own tab, or the same "Unknown tab." a tab of the person or of another agent gets. */
    find(id, tab) {
      const guest = typeof tab === "string" && TAB_ID.test(tab) ? agents.list(id).find((entry) => entry.id === tab) : null;
      if (!guest || guest.view.webContents.isDestroyed()) throw new Error("Unknown tab.");
      touch(guest);
      return guest;
    },
    /** True only for a live guest of the tab table that this agent owns: the engine checks it before every protocol call. */
    holds: (id, guest) => everyGuest().some(([window, entry]) => entry === guest && entry.owner === agentOwner(id) &&
      Boolean(entry.agent) && !window.isDestroyed() && entry.view.webContents !== window.webContents && !entry.view.webContents.isDestroyed()),
    async open(window, agent) {
      const who = validAgent(agent);
      if (stopped || !window || !windows.has(window) || window.isDestroyed()) throw new Error("Browser surface is closed.");
      const guest = await create({ sender: window.webContents }, window, { agent: who });
      touch(guest);
      return guest;
    },
    close(id, tab) {
      const guest = agents.find(id, tab);
      const window = everyGuest().find(([, entry]) => entry === guest)[0];
      closeTab(window, guest.id);
    },
    /**
     * The commit card of an outside agent (ADR 0162, safety 3): shown on that tab until the person answers in the
     * product window. Resolves to true only on their yes; no answer in time, a newer card or a closed tab is a no.
     */
    confirm(id, tab, summary, seconds) {
      const guest = agents.find(id, tab);
      guest.commit?.answer(false);
      return new Promise((resolve) => {
        const timer = setTimeout(() => entry.answer(false), seconds * 1000);
        timer.unref?.();
        const entry = { summary, id: randomBytes(8).toString("hex"), answer: (approved) => {
          clearTimeout(timer);
          if (guest.commit === entry) guest.commit = null;
          resolve(approved === true);
        } };
        guest.commit = entry;
      });
    },
    /** The size the tab's view has when it is shown: the panel's, or a default before the panel was ever laid out. */
    size(guest) {
      const bounds = everyGuest().find(([, entry]) => entry === guest)?.[2].bounds ?? DEFAULT_BOUNDS;
      return { width: bounds.width, height: bounds.height };
    },
    /**
     * The engine is about to send one press or key down to this tab: one event of that kind per channel is the
     * agent's, until the returned function is called or `AGENT_INPUT_MS` passed, whichever is first.
     */
    expect(guest, kind) {
      const until = now() + AGENT_INPUT_MS;
      const entries = INPUT_CHANNELS.map((channel) => ({ channel, kind, until }));
      guest.expected.push(...entries);
      return () => { guest.expected = guest.expected.filter((entry) => !entries.includes(entry)); };
    },
    /** The address policy of every navigation, for the engine's own loads. */
    url: (value) => browserUrl(value, connection()?.origin),
    listen(listener) { agentListeners.add(listener); return () => agentListeners.delete(listener); },
  };
  return {
    agents,
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
      if (action === "diagnostics") {
        const selected = surface?.tabs.get(surface.selected);
        const focused = window.isFocused();
        const visible = selected && !selected.view.webContents.isDestroyed() ? selected.view.getVisible() : null;
        const reason = !selected ? "no guest" : !focused ? "unfocused" : selected.status === "unavailable" ? "unavailable"
          : selected.blank ? "blank" : !surface.shown ? "hidden by frontend" : visible ? "draw-enabled" : "unknown";
        return { focused, visible, status: selected?.status ?? "empty", reason };
      }
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
      if (action === "commit-approve" || action === "commit-refuse") {
        const tab = tabOf(surface, request.tab);
        if (!tab.commit) throw new Error("Unknown tab.");
        // The answer is for the card the person read. A card that came after it is not answered by this click.
        if (typeof request.commit !== "string" || request.commit !== tab.commit.id) throw new Error("Unknown tab.");
        tab.commit.answer(action === "commit-approve");
        return state(window);
      }
      if (action === "take-over" || action === "hand-back" || action === "stop-agent") {
        const tab = tabOf(surface, request.tab);
        if (!tab.agent) throw new Error("Unknown tab.");
        if (action === "stop-agent") {
          // Stop closes every tab of that agent, in every window, and refuses its calls until it is allowed again.
          stoppedAgents.add(tab.agent.id);
          tell("stop", tab);
          closeAgent(tab.agent.id);
        } else if (tab.paused !== (action === "take-over")) {
          tab.paused = action === "take-over";
          tell(action, tab);
        }
        return state(window);
      }
      let guest = request.tab === undefined ? surface?.tabs.get(surface.selected) : tabOf(surface, request.tab);
      // The person steering an agent's tab takes it over, as their own input in the page does.
      if (guest?.agent && !guest.paused && ["navigate", "back", "forward", "reload"].includes(action)) {
        guest.paused = true;
        tell("take-over", guest);
      }
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
    /** The website partition with its configured broker for another guest; `release()` once that guest closes. */
    async lease(page) {
      if (stopped) throw new Error("Browser surface is closed.");
      const session = partitionFor();
      leases += 1;
      let released = false;
      const pages = new Set();
      const release = () => {
        if (released) return;
        released = true;
        for (const page of pages) leasedPages.delete(page);
        leases -= 1;
        releaseNetwork();
      };
      // `attach({ blank, restore })`: how clear takes this guest's page off its site and brings it back.
      const attach = (entry) => { if (!released) { pages.add(entry); leasedPages.add(entry); } };
      // Attached before the broker await, so a clear during opening still blanks and restores this page.
      if (page) attach(page);
      try {
        const network = await networkFor(session);
        return { session, network, release, attach };
      } catch (error) { release(); throw error; }
    },
    openLink,
    /** At quit: never throws, so a window that is already gone cannot keep the app from quitting. */
    closeAll() {
      stopped = true;
      agentListeners.clear();
      for (const window of [...surfaces.keys()]) {
        try { close(window); } catch { /* the other windows are still closed */ }
      }
    },
  };
}
