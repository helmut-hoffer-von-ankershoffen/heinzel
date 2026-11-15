/**
 * The agent tab engine (ADR 0162): what an agent may do in the built-in browser, on its own tabs only.
 *
 * The daemon names an agent and a tab id; the engine takes the guest from the mini browser's tab table, which hands
 * out only tabs that agent opened. It never sees a product window's webContents. Pages are driven through
 * `webContents.debugger` with the fixed method list below; page scripts are the fixed ones in this file, run in an
 * isolated world, never text from an agent or the daemon.
 *
 * Commands and results (the bridge carries them, see browser-bridge.js). `?` marks an optional key.
 *   tabs       {}                                             -> {tabs: [{id, url, title, status, paused}]}
 *              (a tab also carries `sized: false` while it is hidden and could not be given a viewport)
 *   open       {url?}                                         -> {id, url, title, status, paused}
 *   close      {tab}                                          -> {}
 *   navigate   {tab, url} or {tab, to: back|forward|reload}   -> {id, url, title, status, paused}
 *   read       {tab, filter?: all|interactive, ref?, max_chars?} -> {url, title, content, truncated}
 *   text       {tab, max_chars?}                              -> {url, title, content, truncated}
 *   screenshot {tab, scale?}                                  -> {url, width, height, data}   (base64 PNG)
 *   scroll     {tab, direction?, amount?, ref? or x?, y?}     -> {url}
 *   describe   {tab, action: click|type|key, ref? or x?, y?}  -> {target, fingerprint}
 *   click      {tab, ref or x, y, button?, count?, modifiers?, expect} -> {url}
 *   type       {tab, text, ref?, expect}                      -> {url}
 *   key        {tab, keys, expect}                            -> {url}
 *   confirm    {tab, summary, fingerprint, seconds}           -> {approved}   (outside agents only)
 *   allow      {}                                             -> {}           (lifts the person's Stop)
 *
 * Two-step input: `describe` says what the target is; the daemon runs its commit gate and sends the input with
 * `expect` set to the fingerprint (of the description and of the element or point itself). The engine describes the
 * target again just before it acts and refuses with `changed` when anything differs. A click is checked once more
 * between press and release: a recorder in the isolated world says what was really pressed, and a press that landed
 * anywhere else, such as in a frame the page slid under the pointer, is let go outside the page so nothing is clicked.
 * Text and keys reach only the element that had the focus when it was checked: until the input is in, a guard in the
 * isolated world cancels input anywhere else.
 *
 * `target`: {resolved, host, role, name, tag, type, autocomplete, editable, submit, in_form, form_has_password,
 * payment_page, href_host, never}. `never` is null, or why no agent may type there: "password", "one-time-code",
 * "payment-card" or "frame". `resolved: false` means the engine cannot say what the target is.
 *
 * Error codes: invalid, unknown_tab, limit, refused_url, paused, stopped, stale_ref, changed, never, rate, timeout,
 * failed.
 */
import { createHash, randomBytes } from "node:crypto";

export class BridgeError extends Error {
  constructor(code, message) { super(message); this.code = code; }
}

/** Every protocol method the engine may send. No cookie, storage, network, target or browser domain. */
export const CDP_METHODS = Object.freeze([
  "Accessibility.getFullAXTree", "Accessibility.getPartialAXTree",
  "DOM.enable", "DOM.resolveNode", "DOM.getNodeForLocation", "DOM.scrollIntoViewIfNeeded", "DOM.getBoxModel", "DOM.focus",
  "Input.dispatchMouseEvent", "Input.dispatchKeyEvent", "Input.insertText",
  "Page.getFrameTree", "Page.createIsolatedWorld", "Page.getLayoutMetrics", "Page.captureScreenshot",
  "Runtime.callFunctionOn", "Runtime.evaluate",
  // A tab the person is not looking at is a hidden view, and a hidden view has no size: nothing can be hit-tested or
  // laid out in it. This gives such a tab a viewport, and takes it away again when the tab is shown.
  "Emulation.setDeviceMetricsOverride", "Emulation.clearDeviceMetricsOverride",
]);
const WORLD = "hz-browser-agent";
const ALLOWED = new Set(CDP_METHODS);

// The only page scripts. Each runs in an isolated world, so the page can neither see nor replace what they use.
const DESCRIBE = `function () {
  const start = this && this.nodeType === 1 ? this : this && this.parentElement;
  if (!start) return null;
  const el = start.closest("button, a[href], input, select, textarea, summary, label, iframe, frame, object, embed, [role=button], [role=link], [role=menuitem], [role=tab], [role=checkbox], [role=radio], [role=switch], [role=option], [role=textbox], [contenteditable=''], [contenteditable=true], [contenteditable=plaintext-only]") || start;
  const cut = (value) => String(value == null ? "" : value).replace(/\\s+/g, " ").trim().slice(0, 200);
  const attr = (name) => cut(el.getAttribute(name));
  const tag = el.tagName.toLowerCase();
  const type = tag === "input" || tag === "button" ? cut(el.type).toLowerCase() : "";
  const form = el.form || el.closest("form");
  const labelled = tag === "input" && ["button", "submit", "reset"].includes(type);
  const buttonInput = labelled || (tag === "input" && ["image", "checkbox", "radio", "file", "range", "color", "hidden"].includes(type));
  const editable = (tag === "input" && !buttonInput) || tag === "textarea" || tag === "select" || el.isContentEditable === true || attr("role") === "textbox";
  // What only the live page can tell: a field drawn as dots, and one box of a code that is typed digit by digit.
  let secret = null;
  if (editable && tag === "input") {
    const security = getComputedStyle(el).webkitTextSecurity;
    const short = (box) => box.maxLength > 0 && box.maxLength <= 2;
    const scope = form || (el.parentElement && el.parentElement.parentElement) || el.parentElement || el;
    const boxes = Array.from(scope.querySelectorAll("input")).filter(short).length;
    const numeric = /^(numeric|decimal|tel)$/i.test(el.getAttribute("inputmode") || "") || type === "tel" || type === "number";
    if (security && security !== "none") secret = "password";
    else if (scope.querySelector("input[autocomplete~='one-time-code' i]") || (short(el) && (boxes >= 4 || (numeric && boxes >= 3)))) secret = "one-time-code";
  }
  const card = "input[autocomplete^='cc-' i], input[name*='cardnumber' i], input[name*='card-number' i], input[name*='card_number' i], input[name*='cvc' i], input[name*='cvv' i], input[id*='cardnumber' i], input[id*='card-number' i], iframe[name*='card' i], iframe[title*='card' i], iframe[title*='payment' i], iframe[src*='js.stripe.com' i]";
  let href = null;
  if (tag === "a") { try { href = new URL(el.href).host; } catch (error) { href = null; } }
  return {
    tag, type,
    role: attr("role"),
    name: cut(el.getAttribute("aria-label") || (tag === "input" ? (labelled ? el.value : "") : el.innerText) || el.getAttribute("title") || el.getAttribute("alt") || el.getAttribute("placeholder") || ""),
    secret,
    autocomplete: attr("autocomplete").toLowerCase(),
    hints: cut([el.getAttribute("name"), el.id, el.getAttribute("placeholder"), el.getAttribute("aria-label"), el.getAttribute("inputmode")].filter(Boolean).join(" ")),
    editable,
    submit: (tag === "button" && type === "submit" && Boolean(form)) || (tag === "input" && (type === "submit" || type === "image")),
    in_form: Boolean(form),
    form_has_password: Boolean(form && form.querySelector("input[type=password]")),
    payment_page: Boolean(document.querySelector(card)),
    href,
  };
}`;
const ACTIVE = `(() => {
  let active = document.activeElement;
  while (active && active.shadowRoot && active.shadowRoot.activeElement) active = active.shadowRoot.activeElement;
  return !active || active === document.body || active === document.documentElement ? null : active;
})()`;
const FOCUSED = `(() => { const active = ${ACTIVE}; return active ? (${DESCRIBE}).call(active) : null; })()`;
const CONTAINS = "function (other) { return this === other || (typeof this.contains === \"function\" && this.contains(other)); }";
const PAGE_TEXT = "(() => String((document.body && document.body.innerText) || \"\").slice(0, 200000))()";
// One state object per document in the isolated world. Its listeners are the first to be added on the window in
// that world; only trusted events count, so a page cannot fake a press or an input.
const STATE = `(globalThis.__hzAgent || (globalThis.__hzAgent = (() => {
  const state = { path: null, armed: false, allowed: null, blocked: 0 };
  window.addEventListener("pointerdown", (event) => { if (event.isTrusted) state.path = event.composedPath(); }, { capture: true, passive: true });
  const guard = (event) => {
    if (!state.armed || !event.isTrusted) return;
    const target = event.composedPath()[0];
    if (target === state.allowed && !(target && target.type === "password")) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    state.blocked += 1;
  };
  window.addEventListener("keydown", guard, { capture: true });
  window.addEventListener("beforeinput", guard, { capture: true });
  return state;
})()))`;
// Before a press: forget the last one. After it: was the described element really what was pressed?
const ARM_PRESS = `(() => { ${STATE}.path = null; return true; })()`;
const PRESSED = `function () {
  const state = ${STATE};
  const path = state.path;
  state.path = null;
  if (!path || !path.length) return false;
  if (/^(IFRAME|FRAME|OBJECT|EMBED)$/i.test((path[0] && path[0].tagName) || "")) return false;
  return path.includes(this.nodeType === 1 ? this : this.parentElement);
}`;
// Before text or a key: say what has the focus and let input reach only that element until it is disarmed.
const ARM_INPUT = `(() => {
  const state = ${STATE};
  const active = ${ACTIVE};
  state.allowed = active || document.body;
  state.blocked = 0;
  state.armed = true;
  return active ? (${DESCRIBE}).call(active) : null;
})()`;
// The same, for text meant for one element: the focus must be on it or inside it.
const ARM_NODE = `function () {
  const state = ${STATE};
  const active = ${ACTIVE};
  if (!active || !(this === active || (typeof this.contains === "function" && this.contains(active)))) return null;
  state.allowed = active;
  state.blocked = 0;
  state.armed = true;
  return (${DESCRIBE}).call(active);
}`;
const DISARM = `(() => { const state = ${STATE}; state.armed = false; state.allowed = null; return state.blocked; })()`;
/** The fixed page scripts by name; tests tell them apart with this. */
export const PAGE_SCRIPTS = Object.freeze({ DESCRIBE, FOCUSED, CONTAINS, PAGE_TEXT, ARM_PRESS, PRESSED, ARM_INPUT, ARM_NODE, DISARM });
const SCRIPTS = new Set(Object.values(PAGE_SCRIPTS));

const FRAME_TAGS = new Set(["iframe", "frame", "object", "embed"]);
const PASSWORD_HINT = /passw|kennwort|passcode|(?:^|[^a-z])pin(?:[^a-z]|$)/i;
const CODE_HINT = /(?:^|[^a-z])(?:otp|totp|mfa|2fa|tan)(?:[^a-z]|$)|one[-_ ]?time|verification[-_ ]?code|security[-_ ]?code|auth(?:entication)?[-_ ]?code|sms[-_ ]?code/i;
const CARD_HINT = /card[-_ ]?(?:number|num|no)|cardnumber|kartennummer|(?:^|[^a-z])(?:cvc|cvv|csc|ccnum|cc-?number)(?:[^a-z]|$)/i;

/** Why no agent may type into this field, or null. Only the person enters passwords, one-time codes and card data. */
export function neverField(raw) {
  if (!raw) return null;
  if (FRAME_TAGS.has(raw.tag)) return "frame";
  if (!raw.editable) return null;
  if (raw.secret === "password" || raw.secret === "one-time-code") return raw.secret;
  const tokens = String(raw.autocomplete ?? "").split(/\s+/);
  const hints = String(raw.hints ?? "");
  if (raw.type === "password" || tokens.includes("current-password") || tokens.includes("new-password") || PASSWORD_HINT.test(hints))
    return "password";
  if (tokens.includes("one-time-code") || CODE_HINT.test(hints)) return "one-time-code";
  if (tokens.some((token) => token.startsWith("cc-")) || CARD_HINT.test(hints)) return "payment-card";
  return null;
}

const text = (value, limit = 200) => String(value ?? "").replace(/\s+/g, " ").trim().slice(0, limit);
const hostOf = (url) => { try { return new URL(url).host; } catch { return ""; } };
const UNRESOLVED = (host) => ({ resolved: false, host, role: "", name: "", tag: "", type: "", autocomplete: "", editable: false,
  submit: false, in_form: false, form_has_password: false, payment_page: false, href_host: null, never: null });

/** The description the daemon's commit gate reads; fixed keys in a fixed order, so its hash is stable. */
export function targetOf(raw, ax, host) {
  if (!raw || typeof raw !== "object") return UNRESOLVED(host);
  const never = neverField(raw);
  const names = [...new Set([text(ax?.name), text(raw.name)].filter(Boolean))];
  return {
    // What is inside a frame cannot be told from here, so a frame is never a resolved target.
    resolved: !FRAME_TAGS.has(raw.tag), host, role: text(ax?.role || raw.role, 40), name: names.join(" / ").slice(0, 200),
    tag: text(raw.tag, 40), type: text(raw.type, 40), autocomplete: text(raw.autocomplete, 80), editable: raw.editable === true,
    submit: raw.submit === true, in_form: raw.in_form === true, form_has_password: raw.form_has_password === true,
    payment_page: raw.payment_page === true, href_host: typeof raw.href === "string" ? raw.href.slice(0, 255) : null, never,
  };
}

const INTERACTIVE = new Set(["button", "link", "textbox", "searchbox", "combobox", "checkbox", "radio", "switch", "slider",
  "spinbutton", "menuitem", "menuitemcheckbox", "menuitemradio", "tab", "option", "listbox", "treeitem", "textField"]);
const VALUED = new Set(["textbox", "searchbox", "combobox", "slider", "spinbutton"]);
const HIDDEN_ROLES = new Set(["none", "generic", "presentation", "InlineTextBox", "LineBreak", "ListMarker"]);

/**
 * The accessibility tree as indented lines: `- role "name" [ref_N]`. `refOf(backendNodeId, {role, name})` names a
 * node. With `filter: "interactive"` only what can be clicked or typed into is listed.
 */
export function outline(nodes, { filter = "all", root = null, refOf, valueOf = (node) => node.value?.value }) {
  const byId = new Map(nodes.map((node) => [node.nodeId, node]));
  const first = root === null ? nodes.find((node) => node.parentId === undefined) ?? nodes[0]
    : nodes.find((node) => node.backendDOMNodeId === root);
  const lines = [];
  const seen = new Set();
  const walk = (node, depth, above) => {
    if (!node || seen.has(node.nodeId) || depth > 40) return;
    seen.add(node.nodeId);
    const role = String(node.role?.value ?? "");
    const name = text(node.name?.value);
    const focusable = (node.properties ?? []).some((property) => property.name === "focusable" && property.value?.value === true);
    const interactive = INTERACTIVE.has(role) || focusable;
    let shown = !node.ignored && !HIDDEN_ROLES.has(role);
    if (shown && role === "StaticText") shown = filter === "all" && Boolean(name) && name !== above;
    else if (shown) shown = filter === "all" ? Boolean(name) || interactive : interactive;
    if (shown) {
      const ref = role !== "StaticText" && role !== "RootWebArea" && Number.isInteger(node.backendDOMNodeId) ? refOf(node.backendDOMNodeId, { role, name }) : null;
      const held = VALUED.has(role) ? valueOf(node) : undefined;
      const value = held !== undefined ? ` value=${JSON.stringify(text(held, 100))}` : "";
      lines.push(`${"  ".repeat(Math.min(depth, 20))}- ${role === "StaticText" ? "text" : role}${name ? ` ${JSON.stringify(name)}` : ""}${value}${ref ? ` [${ref}]` : ""}`);
    }
    for (const child of node.childIds ?? []) walk(byId.get(child), shown ? depth + 1 : depth, shown ? name : above);
  };
  walk(first, 0, "");
  return lines.join("\n");
}

const MODIFIERS = { alt: 1, option: 1, ctrl: 2, control: 2, meta: 4, cmd: 4, command: 4, shift: 8 };
const NAMED_KEYS = {
  enter: ["Enter", "Enter", 13, "\r"], return: ["Enter", "Enter", 13, "\r"], tab: ["Tab", "Tab", 9], escape: ["Escape", "Escape", 27],
  esc: ["Escape", "Escape", 27], backspace: ["Backspace", "Backspace", 8], delete: ["Delete", "Delete", 46],
  arrowleft: ["ArrowLeft", "ArrowLeft", 37], arrowup: ["ArrowUp", "ArrowUp", 38], arrowright: ["ArrowRight", "ArrowRight", 39],
  arrowdown: ["ArrowDown", "ArrowDown", 40], left: ["ArrowLeft", "ArrowLeft", 37], up: ["ArrowUp", "ArrowUp", 38],
  right: ["ArrowRight", "ArrowRight", 39], down: ["ArrowDown", "ArrowDown", 40], home: ["Home", "Home", 36], end: ["End", "End", 35],
  pageup: ["PageUp", "PageUp", 33], pagedown: ["PageDown", "PageDown", 34], space: [" ", "Space", 32, " "], insert: ["Insert", "Insert", 45],
};
// Keys that never put anything into a field: allowed even while a field no agent may type into has the focus.
const HARMLESS_KEYS = new Set(["Tab", "Escape"]);

/** One key chord such as `Enter`, `ctrl+a` or `shift+Tab`, as the protocol's key event fields. */
export function parseKeys(keys) {
  if (typeof keys !== "string" || !keys || keys.length > 40) throw new BridgeError("invalid", "Name one key, such as Enter or ctrl+a.");
  const parts = keys === "+" ? ["+"] : keys.split("+").map((part) => part.trim());
  if (keys.endsWith("++")) parts.splice(-2, 2, "+");
  const last = parts.pop();
  let modifiers = 0;
  for (const part of parts) {
    const bit = MODIFIERS[part.toLowerCase()];
    if (!bit) throw new BridgeError("invalid", "Name one key, such as Enter or ctrl+a.");
    modifiers |= bit;
  }
  if (!last) throw new BridgeError("invalid", "Name one key, such as Enter or ctrl+a.");
  const named = NAMED_KEYS[last.toLowerCase()];
  let key, code, keyCode, typed;
  if (named) [key, code, keyCode, typed] = named;
  else if (/^f(?:[1-9]|1[0-2])$/i.test(last)) { key = last.toUpperCase(); code = key; keyCode = 111 + Number(last.slice(1)); }
  else if ([...last].length === 1) {
    key = modifiers & 8 ? last.toUpperCase() : last;
    typed = key;
    if (/^[a-z]$/i.test(last)) { code = `Key${last.toUpperCase()}`; keyCode = last.toUpperCase().charCodeAt(0); }
    else if (/^\d$/.test(last)) { code = `Digit${last}`; keyCode = last.charCodeAt(0); }
    else { code = ""; keyCode = 0; }
  } else throw new BridgeError("invalid", "Name one key, such as Enter or ctrl+a.");
  // Copy, cut and paste would read or replace the person's clipboard.
  const command = modifiers & 6;
  if ((command && /^[cvx]$/i.test(key)) || (key === "Insert" && modifiers))
    throw new BridgeError("invalid", "Clipboard shortcuts are not available to agents.");
  // With Ctrl, Alt or Meta held a key is a shortcut and inserts no text.
  const printable = typed !== undefined && !(modifiers & 7);
  return { key, code, windowsVirtualKeyCode: keyCode, modifiers, ...(printable ? { text: typed } : {}) };
}

const MAX_EDGE = 1568;
// Text is put in in pieces, with a look at the focus before each.
const TYPE_CHUNK = 200;
// Field values are shown for this many fields of a page at most; each is checked first.
const MAX_VALUES = 40;
/** Width and height of a PNG from its header, or null. */
export function pngSize(buffer) {
  if (!buffer || buffer.length < 24 || buffer.readUInt32BE(0) !== 0x89504e47 || buffer.toString("latin1", 12, 16) !== "IHDR") return null;
  return { width: buffer.readUInt32BE(16), height: buffer.readUInt32BE(20) };
}

const one = (values) => (value) => values.includes(value);
const int = (low, high) => (value) => Number.isInteger(value) && value >= low && value <= high;
const str = (low, high) => (value) => typeof value === "string" && value.length >= low && value.length <= high;
const TAB = (value) => typeof value === "string" && /^t-[0-9a-f]{8}$/.test(value);
const REF = (value) => typeof value === "string" && /^ref_[1-9]\d{0,8}$/.test(value);
const COORD = int(0, 100000);
const MODIFIER_LIST = (value) => Array.isArray(value) && value.length <= 4 && value.every((item) => typeof item === "string" && Object.hasOwn(MODIFIERS, item.toLowerCase()));
const FINGERPRINT = (value) => typeof value === "string" && /^[0-9a-f]{64}$/.test(value);
// Per command: the keys that must be there, and the ones that may be.
const ARGS = {
  tabs: [{}, {}],
  allow: [{}, {}],
  open: [{}, { url: str(1, 4096) }],
  close: [{ tab: TAB }, {}],
  navigate: [{ tab: TAB }, { url: str(1, 4096), to: one(["back", "forward", "reload"]) }],
  read: [{ tab: TAB }, { filter: one(["all", "interactive"]), ref: REF, max_chars: int(1, 1000000) }],
  text: [{ tab: TAB }, { max_chars: int(1, 1000000) }],
  screenshot: [{ tab: TAB }, { scale: (value) => typeof value === "number" && value >= 0.1 && value <= 1 }],
  scroll: [{ tab: TAB }, { direction: one(["up", "down", "left", "right"]), amount: int(1, 20), ref: REF, x: COORD, y: COORD }],
  describe: [{ tab: TAB, action: one(["click", "type", "key"]) }, { ref: REF, x: COORD, y: COORD }],
  click: [{ tab: TAB, expect: FINGERPRINT }, { ref: REF, x: COORD, y: COORD, button: one(["left", "middle", "right"]), count: int(1, 3), modifiers: MODIFIER_LIST }],
  type: [{ tab: TAB, text: str(1, 10000), expect: FINGERPRINT }, { ref: REF }],
  key: [{ tab: TAB, keys: str(1, 40), expect: FINGERPRINT }, {}],
  confirm: [{ tab: TAB, summary: str(1, 300), fingerprint: FINGERPRINT, seconds: int(1, 120) }, {}],
};
/** The commands the engine knows. */
export const COMMANDS = Object.freeze(Object.keys(ARGS));
const NAVIGATIONS = new Set(["open", "navigate"]);
// The commands that need the page laid out in a viewport.
const SIZED = new Set(["read", "text", "screenshot", "scroll", "describe", "click", "type", "key"]);
// The commands that point at a place in the page: without a viewport they cannot work.
const LAID_OUT = new Set(["scroll", "describe", "click"]);
const SILENT = Symbol("silent");
const hasOnly = (value, keys) => Object.keys(value).length === keys.length && keys.every((key) => Object.hasOwn(value, key));
const COMMANDS_A_MINUTE = 240;
const NAVIGATIONS_A_MINUTE = 20;
const MESSAGES = {
  paused: "The person took over this tab.",
  stopped: "The person stopped this agent's use of the browser.",
  stale: "The page changed, read it again.",
  never: "Only the person may type here: ask the person to take over.",
  url: "Only public HTTPS addresses can be opened.",
  silent: "The page did not answer. Try again in a moment.",
  blank: "This tab has no page yet. Navigate it to an address first.",
  unsized: "This tab has no size while it is hidden, so nothing in it can be pointed at. Ask the person to open it in the browser panel.",
};

function checkArgs(command, args) {
  const spec = Object.hasOwn(ARGS, command) ? ARGS[command] : null;
  if (!spec) throw new BridgeError("invalid", "Unknown browser command.");
  const [required, optional] = spec;
  const fine = args && typeof args === "object" && !Array.isArray(args) &&
    Object.keys(required).every((key) => Object.hasOwn(args, key) && required[key](args[key])) &&
    Object.keys(args).every((key) => Object.hasOwn(required, key) || (Object.hasOwn(optional, key) && optional[key](args[key])));
  if (!fine) throw new BridgeError("invalid", "Invalid browser command.");
  const point = Object.hasOwn(args, "x") || Object.hasOwn(args, "y");
  // A target is a ref or a point, never both and never half a point.
  if (point && (Object.hasOwn(args, "ref") || !Object.hasOwn(args, "x") || !Object.hasOwn(args, "y")))
    throw new BridgeError("invalid", "Invalid browser command.");
  if (command === "navigate" && Object.hasOwn(args, "url") === Object.hasOwn(args, "to")) throw new BridgeError("invalid", "Invalid browser command.");
  if (command === "click" && !point && !Object.hasOwn(args, "ref")) throw new BridgeError("invalid", "Invalid browser command.");
  if (command === "describe" && args.action === "click" && !point && !Object.hasOwn(args, "ref")) throw new BridgeError("invalid", "Invalid browser command.");
  if (command === "describe" && args.action === "key" && (point || Object.hasOwn(args, "ref"))) throw new BridgeError("invalid", "Invalid browser command.");
  if (command === "describe" && args.action === "type" && point) throw new BridgeError("invalid", "Invalid browser command.");
}

/**
 * `tabs` is the mini browser's `agents` table. `window()` is the product window new agent tabs belong to, or null.
 * `loadMs` caps how long a navigation is waited for before the tab's state is returned as it is. `firstMs` caps the
 * first protocol call after the debugger attaches, `sizeMs` the whole attempt to give a hidden tab its viewport.
 */
export function createBrowserAgent({ tabs, window, loadMs = 15000, sizeMs = 3000, firstMs = 5000, now = () => Date.now(),
  log = (line) => console.warn(line) }) {
  const salt = randomBytes(16).toString("hex");
  const pages = new WeakMap();
  const calls = new Map();
  const queues = new Map();

  const pageOf = (guest) => {
    let page = pages.get(guest);
    if (page) return page;
    page = { refs: new Map(), nodes: new Map(), named: new Map(), next: 1, document: 1, sized: null, unsized: false, sent: false, attempt: 0 };
    pages.set(guest, page);
    // The person looks at the tab: it has its real size again at once, whether or not the agent calls again.
    guest.onShown = (shown) => { if (shown) void unsize(guest); };
    const fresh = () => { page.refs.clear(); page.nodes.clear(); page.named.clear(); page.document += 1; };
    const contents = guest.view.webContents;
    // A new main document ends every ref and every fingerprint of the old one.
    contents.on("did-navigate", fresh);
    contents.on("render-process-gone", fresh);
    return page;
  };

  /** The one way to the debugger: an allowed method, a fixed script, and a live tab this agent owns. */
  const cdp = async (id, guest, method, params = {}) => {
    if (!ALLOWED.has(method)) throw new BridgeError("failed", "That browser protocol method is not allowed.");
    const script = method === "Runtime.evaluate" ? params.expression : method === "Runtime.callFunctionOn" ? params.functionDeclaration : null;
    if (method.startsWith("Runtime.") && (!SCRIPTS.has(script) || (method === "Runtime.evaluate" && !Number.isInteger(params.contextId))))
      throw new BridgeError("failed", "That browser protocol method is not allowed.");
    // The isolated world never gets more reach than a content script.
    if (method === "Page.createIsolatedWorld" && (params.grantUniveralAccess || params.grantUniversalAccess))
      throw new BridgeError("failed", "That browser protocol method is not allowed.");
    if (!tabs.holds(id, guest)) throw new BridgeError("unknown_tab", "Unknown tab.");
    // A guest that never loaded a page does not answer the protocol: a call to it would wait forever.
    if (guest.blank) throw new BridgeError("failed", MESSAGES.blank);
    // Only the size of the view the tab has when it is shown, as a plain desktop page: no screen, position, scale or
    // viewport parameter, and never while the person looks at the tab.
    if (method === "Emulation.setDeviceMetricsOverride") {
      const view = tabs.size(guest);
      if (guest.shown || !hasOnly(params, ["width", "height", "deviceScaleFactor", "mobile"]) || params.width !== view.width ||
          params.height !== view.height || params.deviceScaleFactor !== 0 || params.mobile !== false)
        throw new BridgeError("failed", "That browser protocol method is not allowed.");
    }
    if (method === "Emulation.clearDeviceMetricsOverride" && Object.keys(params).length)
      throw new BridgeError("failed", "That browser protocol method is not allowed.");
    const wire = guest.view.webContents.debugger;
    if (!wire.isAttached()) {
      wire.attach("1.3");
      // An override ends with the session that set it.
      Object.assign(pageOf(guest), { sized: null, sent: false });
      // The first call to a guest is the one that may never be answered: it gets a limit, and a guest that stays
      // silent is let go again, so the next call starts afresh.
      let timer;
      const late = new Promise((resolve) => { timer = setTimeout(() => resolve(SILENT), firstMs); timer.unref?.(); });
      let first;
      try { first = await Promise.race([wire.sendCommand("DOM.enable", {}), late]); } finally { clearTimeout(timer); }
      if (first === SILENT) {
        try { wire.detach(); } catch { /* already gone */ }
        throw new BridgeError("timeout", MESSAGES.silent);
      }
    }
    // What counts for clearing is that an override was ever sent on this session, not that its answer came: an
    // answer may be late or never come while the override is in effect all the same.
    if (method === "Emulation.setDeviceMetricsOverride") pageOf(guest).sent = true;
    return wire.sendCommand(method, params);
  };
  /**
   * A hidden view has no size (its page sees a 0 by 0 window), so nothing in it can be laid out or hit-tested. A tab
   * the person is not looking at therefore gets a viewport of the size its view has when shown. It stays while the
   * tab is hidden, so the page keeps its layout and scroll position from one call to the next; it is gone the moment
   * the tab is shown, taken over, handed back or closed.
   */
  const size = async (id, guest) => {
    const state = pageOf(guest);
    if (guest.shown) { await unsize(guest); return; }
    const { width, height } = tabs.size(guest);
    if (state.sized && state.sized.width === width && state.sized.height === height && guest.view.webContents.debugger.isAttached()) return;
    const attempt = ++state.attempt;
    await cdp(id, guest, "Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 0, mobile: false });
    // The answer of an attempt that was given up on, or that a clear overtook, says nothing about now.
    if (state.attempt === attempt) state.sized = { width, height };
  };
  /** Take the override away: always, once one was ever sent on this session. A clear with nothing set does no harm. */
  async function unsize(guest) {
    const state = pageOf(guest);
    state.attempt += 1;
    state.sized = null;
    if (!state.sent || !guest.agent || !guest.view.webContents.debugger.isAttached()) return;
    state.sent = false;
    let timer;
    const late = new Promise((resolve) => { timer = setTimeout(resolve, sizeMs); timer.unref?.(); });
    try { await Promise.race([quiet(() => cdp(guest.agent.id, guest, "Emulation.clearDeviceMetricsOverride")), late]); } finally { clearTimeout(timer); }
  }
  /**
   * Give a hidden tab its viewport if that can be done, within `sizeMs`. Never before the tab's first page: a guest
   * that has not loaded anything may not answer the protocol at all. A tab that cannot be sized is used as it is.
   */
  const fit = async (id, guest) => {
    if (guest.blank) return;
    const state = pageOf(guest);
    let timer;
    const late = new Promise((resolve) => { timer = setTimeout(() => resolve("timeout"), sizeMs); timer.unref?.(); });
    let outcome;
    try { outcome = await Promise.race([quiet(() => size(id, guest)).then(() => "done"), late]); } finally { clearTimeout(timer); }
    // Given up on: whatever that attempt still answers is dropped.
    if (outcome === "timeout") state.attempt += 1;
    // Never silent: a hidden tab without a size says so in its state, refuses what needs a layout, and is logged.
    const unsized = !guest.shown && !state.sized;
    if (unsized && !state.unsized) log(JSON.stringify({ event: "browser.agent.unsized", tab: guest.id, reason: outcome === "timeout" ? "timeout" : "refused" }));
    state.unsized = unsized;
  };
  // The person takes a tab over or hands it back: it is theirs to look at, at its own size.
  tabs.listen(({ event, agent, tab }) => {
    if (event !== "take-over" && event !== "hand-back") return;
    const guest = tabs.list(agent).find((entry) => entry.id === tab);
    if (guest) void unsize(guest);
  });

  const world = async (id, guest) => {
    const { frameTree } = await cdp(id, guest, "Page.getFrameTree");
    const { executionContextId } = await cdp(id, guest, "Page.createIsolatedWorld", { frameId: frameTree.frame.id, worldName: WORLD });
    return executionContextId;
  };
  const objectOf = async (id, guest, backendNodeId, context) =>
    (await cdp(id, guest, "DOM.resolveNode", { backendNodeId, executionContextId: context })).object.objectId;
  const describeNode = async (id, guest, backendNodeId) => {
    try {
      const context = await world(id, guest);
      const objectId = await objectOf(id, guest, backendNodeId, context);
      return (await cdp(id, guest, "Runtime.callFunctionOn", { objectId, functionDeclaration: DESCRIBE, returnByValue: true })).result?.value ?? null;
    } catch (error) {
      if (error instanceof BridgeError && error.code === "unknown_tab") throw error;
      return null;
    }
  };
  const describeFocused = async (id, guest) => {
    try {
      const contextId = await world(id, guest);
      return (await cdp(id, guest, "Runtime.evaluate", { expression: FOCUSED, contextId, returnByValue: true })).result?.value ?? null;
    } catch (error) {
      if (error instanceof BridgeError && error.code === "unknown_tab") throw error;
      return null;
    }
  };
  const nodeAt = async (id, guest, x, y) => {
    try {
      const found = await cdp(id, guest, "DOM.getNodeForLocation", { x, y, includeUserAgentShadowDOM: false, ignorePointerEventsNone: true });
      return Number.isInteger(found?.backendNodeId) ? found.backendNodeId : null;
    } catch (error) {
      if (error instanceof BridgeError && error.code === "unknown_tab") throw error;
      return null;
    }
  };
  const nodeOfRef = (guest, ref) => {
    const node = pageOf(guest).refs.get(ref);
    if (node === undefined) throw new BridgeError("stale_ref", MESSAGES.stale);
    return node;
  };
  const urlOf = (guest) => guest.view.webContents.getURL();
  // Of the description and of the element or point itself, so two look-alike controls never share an approval.
  const fingerprint = (guest, target, anchor) => createHash("sha256")
    .update(JSON.stringify([salt, guest.id, pageOf(guest).document, anchor, target])).digest("hex");
  const quiet = async (work) => {
    try { return await work(); } catch (error) {
      if (error instanceof BridgeError && error.code === "unknown_tab") throw error;
      return null;
    }
  };

  /** What an input would act on: `{target, node}`; `node` is the element for a ref or a point, null for the focus. */
  const resolve = async (id, guest, action, args) => {
    const host = hostOf(urlOf(guest));
    if (Object.hasOwn(args, "ref")) {
      const node = nodeOfRef(guest, args.ref);
      return { node, anchor: ["node", node], target: targetOf(await describeNode(id, guest, node), pageOf(guest).named.get(args.ref), host) };
    }
    if (Object.hasOwn(args, "x")) {
      const node = await nodeAt(id, guest, args.x, args.y);
      const anchor = ["point", args.x, args.y, node];
      if (node === null) return { node: null, anchor, target: UNRESOLVED(host) };
      let ax = null;
      try {
        const [first] = (await cdp(id, guest, "Accessibility.getPartialAXTree", { backendNodeId: node, fetchRelatives: false })).nodes ?? [];
        if (first && !first.ignored) ax = { role: first.role?.value, name: first.name?.value };
      } catch (error) {
        if (error instanceof BridgeError && error.code === "unknown_tab") throw error;
      }
      return { node, anchor, target: targetOf(await describeNode(id, guest, node), ax, host) };
    }
    if (action === "click") throw new BridgeError("invalid", "Invalid browser command.");
    return { node: null, anchor: ["focus"], target: targetOf(await describeFocused(id, guest), null, host) };
  };
  const expected = async (id, guest, action, args) => {
    const found = await resolve(id, guest, action, args);
    if (fingerprint(guest, found.target, found.anchor) !== args.expect) throw new BridgeError("changed", MESSAGES.stale);
    return found;
  };
  const evaluate = async (id, guest, expression, contextId) =>
    (await cdp(id, guest, "Runtime.evaluate", { expression, contextId, returnByValue: true })).result?.value;
  const callOn = async (id, guest, node, context, functionDeclaration, extra = {}) =>
    (await cdp(id, guest, "Runtime.callFunctionOn", { objectId: await objectOf(id, guest, node, context), functionDeclaration,
      returnByValue: true, ...extra })).result?.value;
  /** Is the element at this point the described one, or inside it? */
  const under = async (id, guest, node, at) => {
    const hit = await nodeAt(id, guest, at.x, at.y);
    if (hit === null) return false;
    return (await quiet(async () => {
      const context = await world(id, guest);
      return callOn(id, guest, node, context, CONTAINS, { arguments: [{ objectId: await objectOf(id, guest, hit, context) }] });
    })) === true;
  };
  /**
   * Let input reach only what has the focus now, and say what that is. With `node` the focus must be on that element.
   * Returns the raw description, or null when nothing fitting has the focus; `disarm` tells how much was held back.
   */
  const arm = async (id, guest, node) => quiet(async () => {
    const context = await world(id, guest);
    return (node === null ? await evaluate(id, guest, ARM_INPUT, context) : await callOn(id, guest, node, context, ARM_NODE)) ?? null;
  });
  const disarm = async (id, guest) => {
    const blocked = await quiet(async () => evaluate(id, guest, DISARM, await world(id, guest)));
    return typeof blocked === "number" ? blocked : 1;
  };
  const centre = async (id, guest, node) => {
    try {
      await cdp(id, guest, "DOM.scrollIntoViewIfNeeded", { backendNodeId: node });
      const [x1, y1, x2, y2, x3, y3, x4, y4] = (await cdp(id, guest, "DOM.getBoxModel", { backendNodeId: node })).model.content;
      return { x: Math.round((x1 + x2 + x3 + x4) / 4), y: Math.round((y1 + y2 + y3 + y4) / 4) };
    } catch (error) {
      if (error instanceof BridgeError) throw error;
      throw new BridgeError("stale_ref", MESSAGES.stale);
    }
  };
  const viewCentre = async (id, guest) => {
    const { cssVisualViewport: view } = await cdp(id, guest, "Page.getLayoutMetrics");
    return { x: Math.round(view.clientWidth / 2), y: Math.round(view.clientHeight / 2), width: view.clientWidth, height: view.clientHeight };
  };
  const loaded = (contents, started) => new Promise((resolve) => {
    const done = () => { clearTimeout(timer); contents.off("did-stop-loading", done); resolve(); };
    const timer = setTimeout(done, loadMs);
    contents.on("did-stop-loading", done);
    Promise.resolve(started).then(done, done);
  });
  const tabOf = (guest) => ({ id: guest.id, url: urlOf(guest), title: String(guest.view.webContents.getTitle?.() ?? "").slice(0, 200),
    status: guest.status, paused: guest.paused, ...(pages.get(guest)?.unsized ? { sized: false } : {}) });
  const page = (guest, content, limit) => ({ url: urlOf(guest), title: tabOf(guest).title, content: content.slice(0, limit),
    truncated: content.length > limit });
  const address = (value) => {
    try { return tabs.url(value); } catch { throw new BridgeError("refused_url", MESSAGES.url); }
  };
  const load = async (guest, url) => {
    guest.blank = false;
    const contents = guest.view.webContents;
    await loaded(contents, contents.loadURL(url).catch(() => { guest.status = "unavailable"; }));
  };

  const run = {
    tabs: async (who) => ({ tabs: tabs.list(who.id).map(tabOf) }),
    allow: async (who) => { tabs.allow(who.id); return {}; },
    async open(who, args) {
      const url = Object.hasOwn(args, "url") ? address(args.url) : null;
      const owner = window();
      if (!owner) throw new BridgeError("failed", "The built-in browser needs an open app window.");
      let guest;
      try { guest = await tabs.open(owner, who); } catch (error) {
        if (error instanceof Error && error.message === "Too many tabs.") throw new BridgeError("limit", "Too many tabs: close one first.");
        throw new BridgeError("failed", "The browser could not open a tab.");
      }
      pageOf(guest);
      try {
        // The page first, then its size: a guest that never loaded anything is not touched through the protocol.
        if (url) { await load(guest, url); await fit(who.id, guest); }
        return tabOf(guest);
      } catch (error) {
        // An open that fails leaves no tab behind.
        await quiet(async () => tabs.close(who.id, guest.id));
        throw error;
      }
    },
    close: async (who, args, guest) => { await unsize(guest); tabs.close(who.id, args.tab); return {}; },
    async navigate(who, args, guest) {
      const contents = guest.view.webContents;
      if (Object.hasOwn(args, "url")) await load(guest, address(args.url));
      else {
        const history = contents.navigationHistory;
        const moves = args.to === "reload" || (args.to === "back" ? history.canGoBack() : history.canGoForward());
        if (moves) {
          const done = loaded(contents, new Promise(() => {}));
          if (args.to === "reload") contents.reload();
          else if (args.to === "back") history.goBack();
          else history.goForward();
          await done;
        }
      }
      await fit(who.id, guest);
      return tabOf(guest);
    },
    async read(who, args, guest) {
      const state = pageOf(guest);
      const root = Object.hasOwn(args, "ref") ? nodeOfRef(guest, args.ref) : null;
      const { nodes } = await cdp(who.id, guest, "Accessibility.getFullAXTree");
      if (state.refs.size > 20000) { state.refs.clear(); state.nodes.clear(); state.named.clear(); }
      const refOf = (node, named) => {
        let ref = state.nodes.get(node);
        if (!ref) { ref = `ref_${state.next++}`; state.nodes.set(node, ref); state.refs.set(ref, node); }
        state.named.set(ref, named);
        return ref;
      };
      // What a field holds is shown only for a field an agent may type into, and never on a page that takes a card:
      // the person may have typed a code or a number there while they had taken over.
      const valued = (nodes ?? []).filter((node) => !node.ignored && VALUED.has(node.role?.value) && node.value?.value !== undefined &&
        Number.isInteger(node.backendDOMNodeId));
      const open = new Set();
      for (const node of valued.slice(0, MAX_VALUES)) {
        const raw = await describeNode(who.id, guest, node.backendDOMNodeId);
        if (raw && raw.editable && !raw.payment_page && !neverField(raw)) open.add(node.nodeId);
      }
      const valueOf = (node) => (open.has(node.nodeId) ? node.value.value : undefined);
      return page(guest, outline(nodes ?? [], { filter: args.filter ?? "all", root, refOf, valueOf }), args.max_chars ?? 20000);
    },
    async text(who, args, guest) {
      const contextId = await world(who.id, guest);
      const { result } = await cdp(who.id, guest, "Runtime.evaluate", { expression: PAGE_TEXT, contextId, returnByValue: true });
      return page(guest, typeof result?.value === "string" ? result.value : "", args.max_chars ?? 20000);
    },
    async screenshot(who, args, guest) {
      const scale = args.scale ?? 1;
      const contents = guest.view.webContents;
      if (!tabs.holds(who.id, guest)) throw new BridgeError("unknown_tab", "Unknown tab.");
      // Of the guest view only, also while another tab is shown or the panel is closed.
      const image = await contents.capturePage(undefined, { stayHidden: true });
      const size = image.isEmpty() ? null : image.getSize();
      if (size?.width && size.height) {
        const factor = Math.min(scale, MAX_EDGE / Math.max(size.width, size.height));
        const width = Math.max(1, Math.round(size.width * factor)), height = Math.max(1, Math.round(size.height * factor));
        const png = (factor < 1 ? image.resize({ width, height, quality: "good" }) : image).toPNG();
        return { url: urlOf(guest), ...(pngSize(png) ?? { width, height }), data: png.toString("base64") };
      }
      // A view that never painted gives an empty image: ask the renderer itself.
      const view = await viewCentre(who.id, guest);
      let factor = Math.min(scale, MAX_EDGE / Math.max(view.width, view.height, 1));
      for (let attempt = 0; attempt < 2; attempt += 1) {
        const { data } = await cdp(who.id, guest, "Page.captureScreenshot", { format: "png",
          clip: { x: 0, y: 0, width: view.width, height: view.height, scale: factor } });
        const measured = pngSize(Buffer.from(String(data), "base64"));
        if (measured && Math.max(measured.width, measured.height) <= MAX_EDGE) return { url: urlOf(guest), ...measured, data };
        // A dense display gives more pixels than asked for: once more, smaller.
        if (!measured) break;
        factor *= MAX_EDGE / Math.max(measured.width, measured.height) * 0.99;
      }
      throw new BridgeError("failed", "The page could not be captured.");
    },
    async scroll(who, args, guest) {
      const at = Object.hasOwn(args, "ref") ? await centre(who.id, guest, nodeOfRef(guest, args.ref))
        : Object.hasOwn(args, "x") ? { x: args.x, y: args.y } : await viewCentre(who.id, guest);
      const step = (args.amount ?? 3) * 100;
      const direction = args.direction ?? "down";
      await cdp(who.id, guest, "Input.dispatchMouseEvent", { type: "mouseWheel", x: at.x, y: at.y,
        deltaX: direction === "left" ? -step : direction === "right" ? step : 0,
        deltaY: direction === "up" ? -step : direction === "down" ? step : 0 });
      return { url: urlOf(guest) };
    },
    async describe(who, args, guest) {
      const { target, anchor } = await resolve(who.id, guest, args.action, args);
      return { target, fingerprint: fingerprint(guest, target, anchor) };
    },
    async click(who, args, guest) {
      const id = who.id;
      const byRef = Object.hasOwn(args, "ref");
      const { node, target } = await expected(id, guest, "click", args);
      const at = byRef ? await centre(id, guest, node) : { x: args.x, y: args.y };
      const button = args.button ?? "left";
      const modifiers = (args.modifiers ?? []).reduce((bits, name) => bits | MODIFIERS[name.toLowerCase()], 0);
      const event = { x: at.x, y: at.y, modifiers };
      const mouse = (type, more = {}) => cdp(id, guest, "Input.dispatchMouseEvent", { ...event, type, ...more });
      // Still the described target, and (for a ref) nothing else lying over it where the pointer is?
      const verify = async () => {
        await expected(id, guest, "click", args);
        if (byRef && !(await under(id, guest, node, at)))
          throw new BridgeError("changed", "Something else covers that element. Read the page again.");
      };
      // The pointer goes there first: whatever the page does when it is hovered happens before the checks, not after.
      await mouse("mouseMoved");
      await verify();
      // A frame or an unknown target has no element of this document to be pressed; the checks above and below stand.
      const watched = node !== null && target.resolved;
      for (let count = 1; count <= (args.count ?? 1); count += 1) {
        const context = watched ? await world(id, guest) : null;
        if (watched) await evaluate(id, guest, ARM_PRESS, context);
        await mouse("mousePressed", { button, clickCount: count });
        // Between press and release: the press must have landed on the described element, which must still be it.
        // A press that went into a frame, or to anything else, is let go far outside, so no click happens there.
        const landed = await quiet(async () => {
          if (watched && (await callOn(id, guest, node, context, PRESSED)) !== true) return false;
          try { await verify(); } catch (error) {
            if (error instanceof BridgeError && error.code === "unknown_tab") throw error;
            return false;
          }
          return true;
        });
        if (landed !== true) {
          // Let go far outside the page, with the pointer never moved in between: no element of a frame is under
          // the release, so no widget in a frame is clicked. The page's own document may see a click on itself; a
          // page already runs its own handlers whenever it likes, so that gives it nothing (ADR 0162).
          await quiet(() => mouse("mouseReleased", { x: -1, y: -1, button, clickCount: count }));
          throw new BridgeError("changed", "The page put something else under the pointer. The target was not clicked: read the page again.");
        }
        await mouse("mouseReleased", { button, clickCount: count });
      }
      return { url: urlOf(guest) };
    },
    async type(who, args, guest) {
      const id = who.id;
      const { node, target, anchor } = await expected(id, guest, "type", args);
      if (target.never || !target.resolved) throw new BridgeError("never", MESSAGES.never);
      if (node !== null) {
        try { await cdp(id, guest, "DOM.focus", { backendNodeId: node }); } catch (error) {
          if (error instanceof BridgeError) throw error;
          throw new BridgeError("stale_ref", MESSAGES.stale);
        }
      }
      // Text goes to whatever has the focus, and a page may move it at any time. So before every piece: look at the
      // focus again, and let the page take input only in that one element until the piece is in.
      for (let start = 0; start < args.text.length; start += TYPE_CHUNK) {
        try {
          const focused = await arm(id, guest, node);
          // With a ref the focus must be on that element; a page that sends it elsewhere gets no text at all.
          if (!focused && node !== null) throw new BridgeError("changed", "The page moved the focus away from that field. Read the page again.");
          if (!focused || neverField(focused)) throw new BridgeError("never", MESSAGES.never);
          if (node === null && fingerprint(guest, targetOf(focused, null, hostOf(urlOf(guest))), anchor) !== args.expect)
            throw new BridgeError("changed", MESSAGES.stale);
          await cdp(id, guest, "Input.insertText", { text: args.text.slice(start, start + TYPE_CHUNK) });
        } finally {
          if ((await disarm(id, guest)) > 0) throw new BridgeError("changed", "The page moved the focus while typing. Read the page again.");
        }
      }
      return { url: urlOf(guest) };
    },
    async key(who, args, guest) {
      const id = who.id;
      const chord = parseKeys(args.keys);
      await expected(id, guest, "key", args);
      let down = false;
      try {
        // The same look at the focus in the step that arms the guard, so nothing can move in between.
        const focused = await arm(id, guest, null);
        const target = targetOf(focused, null, hostOf(urlOf(guest)));
        if (fingerprint(guest, target, ["focus"]) !== args.expect) throw new BridgeError("changed", MESSAGES.stale);
        if (target.never && !HARMLESS_KEYS.has(chord.key)) throw new BridgeError("never", MESSAGES.never);
        down = true;
        await cdp(id, guest, "Input.dispatchKeyEvent", { ...chord, type: Object.hasOwn(chord, "text") ? "keyDown" : "rawKeyDown" });
      } finally {
        const { text: _text, ...up } = chord;
        const blocked = await disarm(id, guest);
        if (down) await quiet(() => cdp(id, guest, "Input.dispatchKeyEvent", { ...up, type: "keyUp" }));
        if (blocked > 0) throw new BridgeError("changed", "The page moved the focus before the key. Read the page again.");
      }
      return { url: urlOf(guest) };
    },
    async confirm(who, args, guest) {
      // A Heinzel's commit is confirmed in its chat, never on the tab.
      if (who.kind !== "external") throw new BridgeError("invalid", "Invalid browser command.");
      return { approved: await tabs.confirm(who.id, guest.id, args.summary, args.seconds) };
    },
  };

  const limit = (id, command) => {
    const time = now();
    const recent = (calls.get(id) ?? []).filter((call) => time - call.time < 60000);
    const refuse = recent.length >= COMMANDS_A_MINUTE ||
      (NAVIGATIONS.has(command) && recent.filter((call) => call.navigation).length >= NAVIGATIONS_A_MINUTE);
    if (!refuse) recent.push({ time, navigation: NAVIGATIONS.has(command) });
    calls.set(id, recent);
    if (refuse) {
      const wait = Math.max(1, Math.ceil((60000 - (time - recent[0].time)) / 1000));
      throw new BridgeError("rate", `Too many browser calls. Try again in ${wait} seconds.`);
    }
  };

  const execute = async (command, agent, args) => {
    let who;
    try { who = tabs.identify(agent); } catch { throw new BridgeError("invalid", "Invalid agent."); }
    checkArgs(command, args);
    if (command !== "allow" && tabs.stopped(who.id)) throw new BridgeError("stopped", MESSAGES.stopped);
    limit(who.id, command);
    let guest = null;
    if (Object.hasOwn(args, "tab")) {
      try { guest = tabs.find(who.id, args.tab); } catch { throw new BridgeError("unknown_tab", "Unknown tab."); }
      if (guest.paused) throw new BridgeError("paused", MESSAGES.paused);
      // The tab shows the name the daemon gives the agent now.
      if (guest.agent.name !== who.name || guest.agent.kind !== who.kind) guest.agent = who;
      pageOf(guest);
    }
    try {
      if (guest && SIZED.has(command)) await fit(who.id, guest);
      if (guest && LAID_OUT.has(command) && pageOf(guest).unsized) throw new BridgeError("failed", MESSAGES.unsized);
      const result = await run[command](who, args, guest);
      // The person may have taken over or stopped the agent while the call ran.
      if (guest && command !== "close" && tabs.stopped(who.id)) throw new BridgeError("stopped", MESSAGES.stopped);
      return result;
    } catch (error) {
      if (error instanceof BridgeError) throw error;
      throw new BridgeError("failed", "The browser could not do that.");
    } finally {
      // Never left on a tab the person can see by now.
      if (guest?.shown) await unsize(guest);
    }
  };

  return {
    /** Run one command of one agent. Calls of the same agent run one after the other. Rejects with a BridgeError. */
    handle({ command, agent, args }) {
      const key = typeof agent?.id === "string" ? agent.id : "";
      const turn = (queues.get(key) ?? Promise.resolve()).then(() => execute(command, agent, args));
      const settled = turn.catch(() => {});
      queues.set(key, settled);
      void settled.then(() => { if (queues.get(key) === settled) queues.delete(key); });
      return turn;
    },
    /** For tests: the debugger call with the engine's checks. */
    protocol: cdp,
  };
}
