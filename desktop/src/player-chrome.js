import { fileURLToPath } from "node:url";
import { siblingStack } from "./sibling-stack.js";
import { browserBounds, hasExactKeys } from "./mini-browser-policy.js";

/** @typedef {{action:'play'|'pause'|'redock'|'close'} | {action:'volume'|'resize', value:number} | {action:'move', x:number, y:number}} ChromeCommand */
/** Validate again in main: the preload is a capability, not an authorization boundary. */
export function chromeCommand(request) {
  const fields = { play: [], pause: [], redock: [], close: [], volume: ['value'], resize: ['value'], move: ['x', 'y'] };
  if (!request || !Object.hasOwn(fields, request.action) || !hasExactKeys(request, ['action', ...fields[request.action]]))
    throw new Error('Invalid chrome command.');
  if (request.action === 'volume' && (!Number.isInteger(request.value) || request.value < 0 || request.value > 100)) throw new Error('Invalid volume.');
  if (request.action === 'resize' && (!Number.isInteger(request.value) || request.value < 202 || request.value > 1200)) throw new Error('Invalid width.');
  if (request.action === 'move' && ![request.x, request.y].every((n) => Number.isInteger(n) && Math.abs(n) <= 10000)) throw new Error('Invalid movement.');
  return request;
}

/** Local controls only. Caller must supply the player/card dispatcher and modal visibility decision.
 * No capability is advertised until this surface has loaded and all integration conditions hold.
 */
export async function createPlayerChrome({ window, createView, session, dispatch }) {
  const url = new URL('./player-chrome.html', import.meta.url);
  const view = createView({ webPreferences: { ...(session ? { session } : { partition: "player-chrome" }), preload: fileURLToPath(new URL('./player-chrome-preload.cjs', import.meta.url)), sandbox: true, contextIsolation: true, nodeIntegration: false, webviewTag: false } });
  const contents = view.webContents;
  const owner = window.webContents;
  let shown = false;
  let closed = false;
  view.setVisible(false);
  contents.setWindowOpenHandler(() => ({ action: 'deny' }));
  contents.on('will-navigate', (event) => event.preventDefault());
  contents.on('will-redirect', (event) => event.preventDefault());
  contents.on('will-attach-webview', (event) => event.preventDefault());
  const hide = () => { shown = false; view.setVisible(false); };
  const close = () => {
    if (closed) return;
    closed = true;
    window.off('blur', hide);
    window.off('closed', close);
    owner.off('did-start-navigation', navigate);
    siblingStack(window).release(view);
    if (!contents.isDestroyed()) contents.close({ waitForBeforeUnload: false });
  };
  const navigate = (event) => { if (event.isMainFrame && !event.isSameDocument) close(); };
  window.on('blur', hide);
  window.on('closed', close);
  owner.on('did-start-navigation', navigate);
  try { await contents.loadURL(url.href); } catch (error) { close(); throw error; }
  if (closed) throw new Error('Player controls closed.');
  siblingStack(window).attach(view, 'chrome');
  return {
    owns(event) { return event.sender === contents; },
    layout(bounds, { suppressed, playerReady, dispatcherReady }) {
      if (closed) return false;
      hide();
      if (suppressed || !playerReady || !dispatcherReady || !window.isFocused()) return false;
      view.setBounds(browserBounds(bounds, window.getContentSize()));
      shown = true;
      view.setVisible(true);
      return true;
    },
    handle(event, request) {
      if (closed || !shown || !window.isFocused() || event.sender !== contents || event.senderFrame !== contents.mainFrame || contents.getURL() !== url.href)
        throw new Error('Player controls unavailable.');
      return dispatch(chromeCommand(request));
    },
    hide, close,
  };
}
