/**
 * The browser bridge (ADR 0162): one authenticated WebSocket from the desktop's main process to the daemon's web head.
 * The daemon sends commands for agent tabs, the desktop answers. The web page is not on this path.
 *
 * Wire protocol, version 1. Every frame is one JSON text; both sides check exact keys.
 *   Socket    ws://127.0.0.1:<port>/api/browser-bridge, with `Origin: <the product origin>`.
 *   Hello     desktop: {"hello": "hz-browser-bridge", "version": 1, "token": "<the bridge token>"}
 *             daemon:  {"ready": true, "version": 1}, or it closes the socket. Nothing else is read before that.
 *   Command   daemon:  {"id": "<1-64 chars>", "command": "<name>", "agent": {"id", "name", "kind"}, "args": {...}}
 *             `agent.id` matches [A-Za-z0-9_.:-]{1,80}; `name` (1-60 chars) is shown on the tab; `kind` is
 *             "heinzel" or "external". Commands and their args: browser-agent.js.
 *   Answer    desktop: {"id", "ok": true, "result": {...}} or {"id", "ok": false, "error": {"code", "message"}}
 *   Event     desktop: {"event": "take-over" | "hand-back" | "stop" | "closed", "agent": "<id>", "tab": "<id>" | null}
 *
 * The credential is the bridge token, not the session token `connection()` holds: the product page has that one, and
 * a script in it must not be able to pose as the browser. The main process makes the bridge token at start, hands it
 * to the owned service on `hz ui attach --bridge-token-stdin`, which registers it, and sends it here as the first frame.
 * It never goes into a URL, a window, a preload or an IPC answer. A frame the daemon sends may be 2 MB at most.
 */
import { randomBytes } from "node:crypto";
import { BridgeError, COMMANDS } from "./browser-agent.js";
import { hasExactKeys } from "./mini-browser-policy.js";

export const BRIDGE_PATH = "/api/browser-bridge";
export const BRIDGE_VERSION = 1;
/** The variable `hz ui serve` reads the bridge token from, for a server a test or dev script starts directly. */
export const BRIDGE_TOKEN_ENV = "HEINZEL_UI_BROWSER_BRIDGE";
/** A new bridge token: 32 random bytes, in the alphabet the web head takes for a token. */
export const newBridgeToken = () => randomBytes(32).toString("base64url");
const MAX_FRAME = 2 * 1024 * 1024;
const MAX_RUNNING = 64;
const KNOWN = new Set(COMMANDS);

const plain = (value) => Boolean(value) && typeof value === "object" && !Array.isArray(value);

/**
 * `engine.handle({command, agent, args})` runs one command; `listen(listener)` subscribes to tab events and returns
 * the unsubscribe. `connection()` is the current `{origin, token}` or null; only its origin is used. `token` is the
 * bridge token. `open(url, origin)` makes the socket.
 */
export function createBrowserBridge({ connection, token, engine, listen, open = (url, origin) => new WebSocket(url, { headers: { Origin: origin } }),
  setTimer = setTimeout, clearTimer = clearTimeout, log = () => {} }) {
  let socket = null;
  let ready = false;
  let stopped = true;
  let timer = null;
  let delay = 1000;
  let running = 0;
  let unlisten = null;

  const send = (target, value) => {
    try { if (target === socket && target.readyState === 1) target.send(JSON.stringify(value)); } catch { /* the close handler reconnects */ }
  };
  const retry = () => {
    if (stopped || timer) return;
    timer = setTimer(() => { timer = null; connect(); }, delay);
    timer?.unref?.();
    delay = Math.min(delay * 2, 30000);
  };
  const drop = (target, code, reason) => {
    try { target.close(code, reason); } catch { /* already closed */ }
  };
  const answer = async (target, message) => {
    const fail = (code, text) => send(target, { id: message.id, ok: false, error: { code, message: text } });
    if (!hasExactKeys(message, ["agent", "args", "command", "id"]) || typeof message.command !== "string" ||
        !KNOWN.has(message.command) || !plain(message.agent) || !plain(message.args)) {
      fail("invalid", "Invalid browser command.");
      return;
    }
    if (running >= MAX_RUNNING) { fail("rate", "Too many browser calls at once."); return; }
    running += 1;
    try {
      const result = await engine.handle({ command: message.command, agent: message.agent, args: message.args });
      send(target, { id: message.id, ok: true, result });
    } catch (error) {
      if (error instanceof BridgeError) fail(error.code, error.message);
      else fail("failed", "The browser could not do that.");
    } finally {
      running -= 1;
    }
  };
  const receive = (target, data) => {
    if (target !== socket) return;
    if (typeof data !== "string" || data.length > MAX_FRAME) { drop(target, 1009, "Frame refused."); return; }
    let message;
    try { message = JSON.parse(data); } catch { drop(target, 1007, "Not JSON."); return; }
    if (!plain(message)) { drop(target, 1007, "Not an object."); return; }
    if (!ready) {
      // Only the daemon's ready answer opens the bridge.
      if (hasExactKeys(message, ["ready", "version"]) && message.ready === true && message.version === BRIDGE_VERSION) {
        ready = true;
        delay = 1000;
        log("browser bridge connected");
      } else drop(target, 1002, "Expected ready.");
      return;
    }
    // Without a usable id there is nothing to answer.
    if (typeof message.id !== "string" || !message.id || message.id.length > 64) { drop(target, 1002, "Command without id."); return; }
    void answer(target, message);
  };
  function connect() {
    if (stopped || socket) return;
    const current = connection();
    if (!current) { retry(); return; }
    let target;
    try { target = open(`${current.origin.replace(/^http/, "ws")}${BRIDGE_PATH}`, current.origin); } catch { retry(); return; }
    socket = target;
    ready = false;
    target.addEventListener("open", () => {
      // The bridge token goes to the loopback service this app started, and nowhere else.
      send(target, { hello: "hz-browser-bridge", version: BRIDGE_VERSION, token });
    });
    target.addEventListener("message", (event) => receive(target, event.data));
    const closed = () => {
      if (socket !== target) return;
      socket = null;
      ready = false;
      retry();
    };
    target.addEventListener("close", closed);
    target.addEventListener("error", () => { drop(target); closed(); });
  }
  return {
    start() {
      if (!stopped) return;
      stopped = false;
      unlisten = listen((event) => { if (ready && socket) send(socket, event); });
      connect();
    },
    stop() {
      stopped = true;
      unlisten?.();
      unlisten = null;
      if (timer) clearTimer(timer);
      timer = null;
      const target = socket;
      socket = null;
      ready = false;
      if (target) drop(target, 1000, "Closing.");
    },
    connected: () => ready,
  };
}
