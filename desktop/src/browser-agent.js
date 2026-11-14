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
 * `expect` set to the fingerprint. The engine describes the target again just before it acts and refuses with
 * `changed` when anything differs.
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
]);
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
  const buttonInput = tag === "input" && ["button", "submit", "reset", "image", "checkbox", "radio", "file", "range", "color", "hidden"].includes(type);
  const editable = (tag === "input" && !buttonInput) || tag === "textarea" || tag === "select" || el.isContentEditable === true || attr("role") === "textbox";
  const card = "input[autocomplete^='cc-' i], input[name*='cardnumber' i], input[name*='card-number' i], input[name*='card_number' i], input[name*='cvc' i], input[name*='cvv' i], input[id*='cardnumber' i], input[id*='card-number' i], iframe[name*='card' i], iframe[title*='card' i], iframe[title*='payment' i], iframe[src*='js.stripe.com' i]";
  let href = null;
  if (tag === "a") { try { href = new URL(el.href).host; } catch (error) { href = null; } }
  return {
    tag, type,
    role: attr("role"),
    name: cut(el.getAttribute("aria-label") || (tag === "input" ? (buttonInput ? el.value : "") : el.innerText) || el.getAttribute("title") || el.getAttribute("alt") || el.getAttribute("placeholder") || ""),
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
const FOCUSED = `(() => {
  let active = document.activeElement;
  while (active && active.shadowRoot && active.shadowRoot.activeElement) active = active.shadowRoot.activeElement;
  if (!active || active === document.body || active === document.documentElement) return null;
  return (${DESCRIBE}).call(active);
})()`;
const CONTAINS = "function (other) { return this === other || (typeof this.contains === \"function\" && this.contains(other)); }";
const PAGE_TEXT = "(() => String((document.body && document.body.innerText) || \"\").slice(0, 200000))()";
const SCRIPTS = new Set([DESCRIBE, FOCUSED, CONTAINS, PAGE_TEXT]);

const FRAME_TAGS = new Set(["iframe", "frame", "object", "embed"]);
const PASSWORD_HINT = /passw|kennwort|passcode|(?:^|[^a-z])pin(?:[^a-z]|$)/i;
const CODE_HINT = /(?:^|[^a-z])(?:otp|totp|mfa|2fa|tan)(?:[^a-z]|$)|one[-_ ]?time|verification[-_ ]?code|security[-_ ]?code|auth(?:entication)?[-_ ]?code|sms[-_ ]?code/i;
const CARD_HINT = /card[-_ ]?(?:number|num|no)|cardnumber|kartennummer|(?:^|[^a-z])(?:cvc|cvv|csc|ccnum|cc-?number)(?:[^a-z]|$)/i;

/** Why no agent may type into this field, or null. Only the person enters passwords, one-time codes and card data. */
export function neverField(raw) {
  if (!raw) return null;
  if (FRAME_TAGS.has(raw.tag)) return "frame";
  if (!raw.editable) return null;
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
export function outline(nodes, { filter = "all", root = null, refOf }) {
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
      const value = VALUED.has(role) && node.value?.value !== undefined ? ` value=${JSON.stringify(text(node.value.value, 100))}` : "";
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
const COMMANDS_A_MINUTE = 240;
const NAVIGATIONS_A_MINUTE = 20;
const MESSAGES = {
  paused: "The person took over this tab.",
  stopped: "The person stopped this agent's use of the browser.",
  stale: "The page changed, read it again.",
  never: "Only the person may type here: ask the person to take over.",
  url: "Only public HTTPS addresses can be opened.",
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
 * `loadMs` caps how long a navigation is waited for before the tab's state is returned as it is.
 */
export function createBrowserAgent({ tabs, window, loadMs = 15000, now = () => Date.now() }) {
  const salt = randomBytes(16).toString("hex");
  const pages = new WeakMap();
  const calls = new Map();
  const queues = new Map();

  const pageOf = (guest) => {
    let page = pages.get(guest);
    if (page) return page;
    page = { refs: new Map(), nodes: new Map(), named: new Map(), next: 1, document: 1 };
    pages.set(guest, page);
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
    if (!tabs.holds(id, guest)) throw new BridgeError("unknown_tab", "Unknown tab.");
    const wire = guest.view.webContents.debugger;
    if (!wire.isAttached()) {
      wire.attach("1.3");
      await wire.sendCommand("DOM.enable", {});
    }
    return wire.sendCommand(method, params);
  };

  const world = async (id, guest) => {
    const { frameTree } = await cdp(id, guest, "Page.getFrameTree");
    const { executionContextId } = await cdp(id, guest, "Page.createIsolatedWorld", { frameId: frameTree.frame.id, worldName: "hz-browser-agent" });
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
  const fingerprint = (guest, target) => createHash("sha256")
    .update(JSON.stringify([salt, guest.id, pageOf(guest).document, target])).digest("hex");

  /** What an input would act on: `{target, node}`; `node` is the element for a ref or a point, null for the focus. */
  const resolve = async (id, guest, action, args) => {
    const host = hostOf(urlOf(guest));
    if (Object.hasOwn(args, "ref")) {
      const node = nodeOfRef(guest, args.ref);
      return { node, target: targetOf(await describeNode(id, guest, node), pageOf(guest).named.get(args.ref), host) };
    }
    if (Object.hasOwn(args, "x")) {
      const node = await nodeAt(id, guest, args.x, args.y);
      if (node === null) return { node: null, target: UNRESOLVED(host) };
      let ax = null;
      try {
        const [first] = (await cdp(id, guest, "Accessibility.getPartialAXTree", { backendNodeId: node, fetchRelatives: false })).nodes ?? [];
        if (first && !first.ignored) ax = { role: first.role?.value, name: first.name?.value };
      } catch (error) {
        if (error instanceof BridgeError && error.code === "unknown_tab") throw error;
      }
      return { node, target: targetOf(await describeNode(id, guest, node), ax, host) };
    }
    if (action === "click") throw new BridgeError("invalid", "Invalid browser command.");
    return { node: null, target: targetOf(await describeFocused(id, guest), null, host) };
  };
  const expected = async (id, guest, action, args) => {
    const found = await resolve(id, guest, action, args);
    if (fingerprint(guest, found.target) !== args.expect) throw new BridgeError("changed", MESSAGES.stale);
    return found;
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
    status: guest.status, paused: guest.paused });
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
      if (url) await load(guest, url);
      return tabOf(guest);
    },
    close: async (who, args) => { tabs.close(who.id, args.tab); return {}; },
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
      return page(guest, outline(nodes ?? [], { filter: args.filter ?? "all", root, refOf }), args.max_chars ?? 20000);
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
      const { target } = await resolve(who.id, guest, args.action, args);
      return { target, fingerprint: fingerprint(guest, target) };
    },
    async click(who, args, guest) {
      const { node } = await expected(who.id, guest, "click", args);
      let at = { x: args.x, y: args.y };
      if (Object.hasOwn(args, "ref")) {
        at = await centre(who.id, guest, node);
        // The click lands where the pointer is: refuse when something else lies over the described target.
        const hit = await nodeAt(who.id, guest, at.x, at.y);
        let inside = false;
        if (hit !== null) {
          try {
            const context = await world(who.id, guest);
            const [objectId, other] = [await objectOf(who.id, guest, node, context), await objectOf(who.id, guest, hit, context)];
            inside = (await cdp(who.id, guest, "Runtime.callFunctionOn", { objectId, functionDeclaration: CONTAINS,
              arguments: [{ objectId: other }], returnByValue: true })).result?.value === true;
          } catch (error) {
            if (error instanceof BridgeError && error.code === "unknown_tab") throw error;
          }
        }
        if (!inside) throw new BridgeError("changed", "Something else covers that element. Read the page again.");
        // Scrolling it into view must not have changed what it is.
        await expected(who.id, guest, "click", args);
      }
      const button = args.button ?? "left";
      const modifiers = (args.modifiers ?? []).reduce((bits, name) => bits | MODIFIERS[name.toLowerCase()], 0);
      const event = { x: at.x, y: at.y, modifiers };
      await cdp(who.id, guest, "Input.dispatchMouseEvent", { ...event, type: "mouseMoved" });
      for (let count = 1; count <= (args.count ?? 1); count += 1) {
        await cdp(who.id, guest, "Input.dispatchMouseEvent", { ...event, type: "mousePressed", button, clickCount: count });
        await cdp(who.id, guest, "Input.dispatchMouseEvent", { ...event, type: "mouseReleased", button, clickCount: count });
      }
      return { url: urlOf(guest) };
    },
    async type(who, args, guest) {
      const { node, target } = await expected(who.id, guest, "type", args);
      if (target.never || !target.resolved) throw new BridgeError("never", MESSAGES.never);
      if (node !== null) {
        try { await cdp(who.id, guest, "DOM.focus", { backendNodeId: node }); } catch (error) {
          if (error instanceof BridgeError) throw error;
          throw new BridgeError("stale_ref", MESSAGES.stale);
        }
      }
      // The text goes to whatever has the focus now: a page may have moved it, so look again.
      const focused = await describeFocused(who.id, guest);
      if (!focused || neverField(focused)) throw new BridgeError("never", MESSAGES.never);
      await cdp(who.id, guest, "Input.insertText", { text: args.text });
      return { url: urlOf(guest) };
    },
    async key(who, args, guest) {
      const chord = parseKeys(args.keys);
      const { target } = await expected(who.id, guest, "key", args);
      if (target.never && !HARMLESS_KEYS.has(chord.key)) throw new BridgeError("never", MESSAGES.never);
      await cdp(who.id, guest, "Input.dispatchKeyEvent", { ...chord, type: Object.hasOwn(chord, "text") ? "keyDown" : "rawKeyDown" });
      const { text: _text, ...up } = chord;
      await cdp(who.id, guest, "Input.dispatchKeyEvent", { ...up, type: "keyUp" });
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
      const result = await run[command](who, args, guest);
      // The person may have taken over or stopped the agent while the call ran.
      if (guest && command !== "close" && tabs.stopped(who.id)) throw new BridgeError("stopped", MESSAGES.stopped);
      return result;
    } catch (error) {
      if (error instanceof BridgeError) throw error;
      throw new BridgeError("failed", "The browser could not do that.");
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
