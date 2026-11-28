import { siblingStack } from "./sibling-stack.js";
import { browserBounds, browserOwner, browserUrl, hasExactKeys, nativeSurfaceDiagnostics } from "./mini-browser-policy.js";
import { classifyUiLink, createExternalOpener, createUserActivation, grantOnNativeInput } from "./ui-navigation.js";

/**
 * The media player's YouTube embed as a native guest on the sidebar browser's website partition (ADR 0140), so a
 * sign-in in either counts in both. The product session stays apart: the guest has no preload, no product cookie or
 * token and cannot reach the local service. As a top-level page the embed has no parent for the IFrame API's
 * postMessage protocol, so a narrow bridge calls the player element's own methods: play, pause, volume and its state.
 */
export const PLAYER_ORIGIN = "https://www.youtube.com";
const VIDEO = /^[A-Za-z0-9_-]{11}$/;
const STATES = new Set([-1, 0, 1, 2, 3, 5]);
const PLAYER = 'document.getElementById("movie_player")';
const SCRIPT_TIMEOUT_MS = 2000;
// EU visitors are sent to YouTube's consent page before the embed; it is the one top-level detour allowed.
const CONSENT_ORIGIN = "https://consent.youtube.com";

/** The embed address for one video id; nothing else from the renderer reaches the URL. */
export function playerUrl(video) {
  if (typeof video !== "string" || !VIDEO.test(video)) throw new Error("Unknown video.");
  const url = new URL(`/embed/${video}`, PLAYER_ORIGIN);
  url.searchParams.set("playsinline", "1");
  url.searchParams.set("rel", "0");
  return url.href;
}

/** Fixed scripts for the embed's page; only a validated integer volume is put into one. */
export function playerScript(action, value) {
  const call = (body) => `(() => { const p = ${PLAYER}; if (!p || typeof p.playVideo !== "function") return false; ${body} return true; })()`;
  if (action === "play") return call("p.playVideo();");
  if (action === "pause") return call("p.pauseVideo();");
  if (action === "volume") {
    if (!Number.isInteger(value) || value < 0 || value > 100) throw new Error("Invalid player volume.");
    return call(`p.setVolume(${value}); if (${value} > 0 && p.isMuted && p.isMuted()) p.unMute();`);
  }
  if (action === "state")
    return `(() => { const p = ${PLAYER}; const ready = Boolean(p && typeof p.getPlayerState === "function");
      return { ready, state: ready ? p.getPlayerState() : -1, error: Boolean(document.querySelector(".ytp-error")) }; })()`;
  throw new Error("Invalid player command.");
}

/** The page answers in its own world: keep only the known shape. */
export function playerState(value) {
  const state = value && typeof value === "object" && STATES.has(value.state) ? value.state : -1;
  return { ready: value?.ready === true, state, error: value?.error === true };
}

const FIELDS = { open: ["action", "video"], layout: ["action", "bounds"], hide: ["action"], close: ["action"],
  play: ["action"], pause: ["action"], volume: ["action", "value"], state: ["action"], diagnostics: ["action"] };

export function createMediaPlayer({ windows, fromWebContents, connection, lease, createView, openExternal,
  now = Date.now, platform = process.platform }) {
  const players = new Map();
  const generations = new WeakMap();
  let stopped = false;
  const close = (window) => {
    generations.set(window, (generations.get(window) ?? 0) + 1);
    const player = players.get(window);
    if (!player) return;
    players.delete(window);
    player.detach();
    // The window may be destroyed already (it closes before the app quits): the lease is given back all the same.
    try {
      siblingStack(window).release(player.view);
      if (!player.view.webContents.isDestroyed()) player.view.webContents.close({ waitForBeforeUnload: false });
    } catch { /* destroyed with its window */ }
    player.release();
  };
  const open = async (window, video) => {
    const url = playerUrl(video);
    const generation = generations.get(window) ?? 0;
    // The page is known to the partition's clear before the guest exists; it acts once the view is built.
    const page = { blank: () => Promise.resolve(), restore: () => {} };
    const { session, network, release } = await lease(page);
    try {
      return build(window, video, url, generation, { session, network, release, page });
    } catch (error) {
      release();
      throw error;
    }
  };
  const build = (window, video, url, generation, { session, network, release, page }) => {
    const product = connection();
    if (!product || stopped || window.isDestroyed() || (generations.get(window) ?? 0) !== generation)
      throw new Error("Player surface is closed.");
    const view = createView({ webPreferences: {
      session, sandbox: true, contextIsolation: true, nodeIntegration: false, nodeIntegrationInSubFrames: false,
      webviewTag: false, navigateOnDragDrop: false, safeDialogs: true, disableDialogs: true,
      // As for every guest of the website partition: spell check only where it downloads nothing (ADR 0140).
      spellcheck: platform === "darwin",
    } });
    const contents = view.webContents;
    const player = { view, video, release, status: "loading" };
    contents.setWebRTCIPHandlingPolicy("disable_non_proxied_udp");
    contents.on("select-client-certificate", (event, ...args) => { event.preventDefault(); args.at(-1)(); });
    contents.on("login", (event, _details, info, callback) => { event.preventDefault(); network.authenticate(info, callback); });
    contents.on("will-prevent-unload", (event) => event.preventDefault());
    contents.on("will-attach-webview", (event) => event.preventDefault());
    // Electron would pick the first Bluetooth device itself; the player never needs one.
    contents.on("select-bluetooth-device", (event, _devices, callback) => { event.preventDefault(); callback(""); });
    // The embed's own links ("Learn more", "Sign in", "Watch on YouTube") open in the default browser, only right
    // after the person's input in the player, classified and rate limited like every product link.
    const activation = createUserActivation({ now });
    grantOnNativeInput(contents, activation);
    const opener = createExternalOpener({ connection: product, openExternal, now });
    const outside = (target) => {
      if (classifyUiLink(target, connection()).kind === "external" && activation.consume()) opener(target);
    };
    contents.setWindowOpenHandler((details) => { outside(details?.url); return { action: "deny" }; });
    contents.on("will-navigate", (event) => {
      try {
        const next = new URL(event.url);
        if ((next.origin === PLAYER_ORIGIN && next.pathname === `/embed/${video}`) || next.origin === CONSENT_ORIGIN) return;
      } catch { /* not a URL */ }
      event.preventDefault();
      outside(event.url);
    });
    const guard = (event) => {
      try { browserUrl(event.url, connection()?.origin); } catch { event.preventDefault(); }
    };
    contents.on("will-frame-navigate", (event) => { if (!event.isMainFrame) guard(event); });
    contents.on("will-redirect", (event) => {
      if (event.isMainFrame === false) { guard(event); return; }
      try {
        const next = new URL(event.url);
        if ((next.origin === PLAYER_ORIGIN && next.pathname === `/embed/${video}`) || next.origin === CONSENT_ORIGIN) return;
      } catch { /* not a URL */ }
      event.preventDefault();
    });
    contents.on("did-stop-loading", () => { if (player.status === "loading") player.status = "ready"; });
    contents.on("did-fail-load", (_event, code, _description, _url, mainFrame) => {
      if (mainFrame && code !== -3) player.status = "unavailable";
    });
    contents.on("render-process-gone", () => { player.status = "unavailable"; view.setVisible(false); });
    siblingStack(window).attach(view, "media");
    view.setVisible(false);
    players.set(window, player);
    const onClosed = () => close(window);
    const onNavigation = (event) => { if (event.isMainFrame && !event.isSameDocument) close(window); };
    const onBlur = () => view.setVisible(false);
    // Kept from the start: a destroyed window no longer hands out its webContents ("Object has been destroyed").
    const owner = window.webContents;
    player.detach = () => {
      for (const [emitter, name, listener] of [[window, "closed", onClosed], [window, "blur", onBlur], [owner, "did-start-navigation", onNavigation]]) {
        try { emitter.off(name, listener); } catch { /* gone, and its listeners with it */ }
      }
    };
    window.on("closed", onClosed);
    window.on("blur", onBlur);
    owner.on("did-start-navigation", onNavigation);
    // YouTube requires a referrer for embeds; it is the product's origin, as the web head's frame sends today.
    const load = () => {
      if (contents.isDestroyed()) return;
      player.status = "loading";
      void contents.loadURL(url, { httpReferrer: `${product.origin}/` }).catch(() => { player.status = "unavailable"; });
    };
    page.blank = () => (contents.isDestroyed() ? Promise.resolve() : contents.loadURL("about:blank"));
    page.restore = load;
    load();
    return player;
  };
  // The script is built (and its value checked) before the page runs it; only the page's own failure is tolerated.
  const run = async (player, action, value) => {
    const script = playerScript(action, value);
    const contents = player.view.webContents;
    if (contents.isDestroyed()) return null;
    // Never a user activation for the page (no gesture), and never waiting on a stuck page.
    let timer;
    const timeout = new Promise((resolve) => { timer = setTimeout(() => resolve(null), SCRIPT_TIMEOUT_MS); });
    try { return await Promise.race([contents.executeJavaScript(script, false), timeout]); }
    catch { return null; }
    finally { clearTimeout(timer); }
  };
  const layout = (window, event, player, requested) => {
    try {
      const zoom = event.sender.getZoomFactor();
      const size = window.getContentSize();
      const css = browserBounds(requested, size.map((dimension) => dimension / zoom));
      const bounds = Object.fromEntries(Object.entries(css).map(([key, value]) => [key, Math.round(value * zoom)]));
      player.view.setBounds(browserBounds(bounds, size));
    } catch (error) {
      player.view.setVisible(false);
      throw error;
    }
    player.view.setVisible(window.isFocused() && player.status !== "unavailable");
  };
  return {
    async handle(event, request) {
      if (stopped) throw new Error("Player surface is closed.");
      const window = browserOwner(event, { windows, fromWebContents, connection });
      if (!request || typeof request !== "object" || !Object.hasOwn(FIELDS, request.action) ||
          !hasExactKeys(request, FIELDS[request.action])) throw new Error("Invalid player command.");
      if (request.action === "close") { close(window); return { status: "closed" }; }
      let player = players.get(window);
      if (request.action === "diagnostics") return nativeSurfaceDiagnostics(window, player);
      if (request.action === "volume") playerScript("volume", request.value);
      if (request.action === "open") {
        playerUrl(request.video);
        if (player?.video !== request.video) {
          close(window);
          player = await open(window, request.video);
        }
      }
      if (!player) return { status: "empty", ready: false, state: -1, error: false };
      if (request.action === "layout") layout(window, event, player, request.bounds);
      if (request.action === "hide") player.view.setVisible(false);
      if (["play", "pause", "volume"].includes(request.action)) await run(player, request.action, request.value);
      const state = playerState(await run(player, "state"));
      return { status: player.status, ...state };
    },
    /** At quit: never throws, so a window that is already gone cannot keep the app from quitting. */
    closeAll() {
      stopped = true;
      for (const window of [...players.keys()]) {
        try { close(window); } catch { /* the other windows are still closed */ }
      }
    },
  };
}
