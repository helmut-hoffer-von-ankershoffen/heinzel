import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test from "node:test";
import { BridgeError, CDP_METHODS, PAGE_SCRIPTS, createBrowserAgent, neverField, outline, parseKeys, pngSize, targetOf } from "../src/browser-agent.js";
import { AGENT_IDLE_MS, createMiniBrowser } from "../src/mini-browser.js";

const HEINZEL = { id: "chat-1", name: "Ada", kind: "heinzel" };
const OTHER = { id: "chat-2", name: "Bo", kind: "heinzel" };
const OUTSIDE = { id: "ci-bot", name: "ci-bot", kind: "external" };
const BUTTON = { tag: "button", type: "button", role: "", name: "More", autocomplete: "", hints: "", editable: false, submit: false,
  in_form: false, form_has_password: false, payment_page: false, href: null };
const FIELD = { ...BUTTON, tag: "input", type: "text", name: "", hints: "q", editable: true };
const PASSWORD = { ...FIELD, type: "password", hints: "pw", in_form: true, form_has_password: true };

function png(width, height) {
  const buffer = Buffer.alloc(24);
  buffer.writeUInt32BE(0x89504e47, 0); buffer.writeUInt32BE(0x0d0a1a0a, 4); buffer.writeUInt32BE(13, 8);
  buffer.write("IHDR", 12, "latin1"); buffer.writeUInt32BE(width, 16); buffer.writeUInt32BE(height, 20);
  return buffer;
}
function image(width, height) {
  return { isEmpty: () => !width, getSize: () => ({ width, height }), toPNG: () => png(width, height),
    resize: (size) => image(size.width, size.height) };
}

/** A real tab table over fake Electron objects; each guest gets a scripted debugger and a `page` to describe. */
function fixture() {
  const frame = { processId: 1, frameToken: "main", url: "http://127.0.0.1:8737/", detached: false, isDestroyed: () => false };
  const productWire = { attached: 0, isAttached: () => false, attach: () => { productWire.attached += 1; }, sendCommand: async () => { productWire.attached += 1; } };
  const sender = Object.assign(new EventEmitter(), { mainFrame: frame, isDestroyed: () => false, getZoomFactor: () => 1, debugger: productWire });
  const views = [];
  const owner = Object.assign(new EventEmitter(), { webContents: sender, isDestroyed: () => false, isFocused: () => true,
    getContentSize: () => [1000, 700], contentView: { addChildView: () => {}, removeChildView: () => {} } });
  const controller = createMiniBrowser({
    createNetwork: async () => ({ configure: async () => {}, close: () => {}, authenticate: (_info, callback) => callback() }),
    windows: new WeakSet([owner]), fromWebContents: (contents) => (contents === sender ? owner : null),
    connection: () => ({ origin: "http://127.0.0.1:8737", token: "a".repeat(32) }),
    createSession: () => Object.assign(new EventEmitter(), { clearStorageData: async () => {}, setPermissionCheckHandler: () => {},
      setPermissionRequestHandler: () => {}, setDevicePermissionHandler: () => {}, setDisplayMediaRequestHandler: () => {},
      webRequest: { onBeforeRequest: () => {} } }),
    createView: () => {
      let url = "", dead = false, attached = false;
      // `pressed`: whether a press lands on the described element; `blocked`: input the guard held back; `on`: what
      // the page does when a protocol call reaches it.
      const page = { elements: {}, focused: null, at: null, covered: false, text: "Hello page", shot: image(800, 600), ax: [],
        pressed: true, blocked: 0, armed: null, on: () => {} };
      const sent = [];
      const wire = { isAttached: () => attached, attach: (version) => { attached = true; sent.push(["attach", version]); },
        detach: () => { attached = false; sent.push(["detach"]); },
        sendCommand: async (method, params) => {
          sent.push([method, params]);
          page.on(method, params);
          if (method === "Page.getFrameTree") return { frameTree: { frame: { id: "F" } } };
          if (method === "Page.createIsolatedWorld") return { executionContextId: 7 };
          if (method === "Page.getLayoutMetrics") return { cssVisualViewport: { clientWidth: 800, clientHeight: 600 } };
          if (method === "Page.captureScreenshot") return { data: png(Math.round(800 * params.clip.scale * (page.dense ?? 1)), Math.round(600 * params.clip.scale * (page.dense ?? 1))).toString("base64") };
          if (method === "Accessibility.getFullAXTree") return { nodes: page.ax };
          if (method === "Accessibility.getPartialAXTree") return { nodes: [] };
          if (method === "DOM.resolveNode") {
            if (!Object.hasOwn(page.elements, params.backendNodeId)) throw new Error("No node");
            return { object: { objectId: `o${params.backendNodeId}` } };
          }
          if (method === "DOM.getNodeForLocation") { if (page.at === null) throw new Error("No node"); return { backendNodeId: page.at }; }
          if (method === "DOM.getBoxModel") return { model: { content: [10, 10, 30, 10, 30, 20, 10, 20] } };
          if (method === "DOM.focus") { page.onFocus?.(params.backendNodeId); return {}; }
          if (method === "Runtime.callFunctionOn") {
            const node = Number(params.objectId.slice(1)), script = params.functionDeclaration;
            if (script === PAGE_SCRIPTS.CONTAINS) return { result: { value: !page.covered } };
            if (script === PAGE_SCRIPTS.PRESSED) return { result: { value: page.pressed } };
            // The focus must be on the element the text is for.
            if (script === PAGE_SCRIPTS.ARM_NODE) { page.armed = page.focused === page.elements[node] ? node : null; return { result: { value: page.armed === null ? null : page.focused } }; }
            return { result: { value: page.elements[node] } };
          }
          if (method === "Runtime.evaluate") {
            const script = params.expression;
            if (script === PAGE_SCRIPTS.ARM_INPUT) { page.armed = "focus"; return { result: { value: page.focused } }; }
            if (script === PAGE_SCRIPTS.DISARM) { page.armed = null; const blocked = page.blocked; page.blocked = 0; return { result: { value: blocked } }; }
            if (script === PAGE_SCRIPTS.ARM_PRESS) return { result: { value: true } };
            return { result: { value: script === PAGE_SCRIPTS.FOCUSED ? page.focused : page.text } };
          }
          return {};
        } };
      const contents = Object.assign(new EventEmitter(), { isDestroyed: () => dead, mainFrame: { top: null, origin: "", url: "" },
        close: () => { dead = true; }, getURL: () => url, getTitle: () => "Title", debugger: wire,
        loadURL: async (target) => { url = target; sent.push(["load", target]); contents.emit("did-navigate"); }, reload: () => { sent.push(["reload"]); contents.emit("did-stop-loading"); },
        navigationHistory: { canGoBack: () => false, canGoForward: () => false },
        capturePage: async (_rect, options) => { sent.push(["capture", options]); return page.shot; },
        setWebRTCIPHandlingPolicy: () => {}, setWindowOpenHandler: (handler) => { contents.popup = handler; } });
      const view = { webContents: contents, page, sent, setBounds: (bounds) => sent.push(["bounds", bounds]), setVisible: (visible) => sent.push(["visible", visible]) };
      views.push(view); return view;
    },
  });
  const events = [];
  controller.agents.listen((event) => events.push(event));
  let time = 0;
  const engine = createBrowserAgent({ tabs: controller.agents, window: () => owner, loadMs: 5, now: () => time });
  const call = (agent, command, args = {}) => engine.handle({ command, agent, args });
  return { controller, engine, owner, sender, productWire, views, events, call, tick: (ms) => { time += ms; },
    invoke: (request) => controller.handle({ sender, senderFrame: frame }, request),
    refused: (promise, code) => assert.rejects(promise, (error) => error instanceof BridgeError && error.code === code) };
}
const methods = (view) => view.sent.map(([method]) => method);
/** An open agent tab on a page with one button (node 5, ref_1) and one text field (node 6, ref_2). */
async function onPage(f, agent = HEINZEL) {
  const tab = await f.call(agent, "open", { url: "https://shop.example.com/" });
  const view = f.views.at(-1);
  view.page.elements = { 5: BUTTON, 6: FIELD, 9: PASSWORD };
  view.page.ax = [
    { nodeId: "1", childIds: ["2", "3", "4"], role: { value: "RootWebArea" }, name: { value: "Shop" }, backendDOMNodeId: 1 },
    { nodeId: "2", parentId: "1", role: { value: "button" }, name: { value: "More" }, backendDOMNodeId: 5 },
    { nodeId: "3", parentId: "1", role: { value: "textbox" }, name: { value: "Search" }, backendDOMNodeId: 6, value: { value: "shoes" } },
    { nodeId: "4", parentId: "1", role: { value: "textbox" }, name: { value: "Password" }, backendDOMNodeId: 9, value: { value: "hunter2" } },
  ];
  await f.call(agent, "read", { tab: tab.id });
  return { tab: tab.id, view };
}

test("agent tabs: opaque ids, the agent's name, the shared partition, never the person's selection", async () => {
  const f = fixture();
  await f.invoke({ action: "navigate", url: "https://one.example.com" });
  const mine = (await f.invoke({ action: "state" })).selected;
  const tab = await f.call(HEINZEL, "open", { url: "https://two.example.com/a" });
  assert.match(tab.id, /^t-[0-9a-f]{8}$/);
  assert.deepEqual(tab, { id: tab.id, url: "https://two.example.com/a", title: "Title", status: tab.status, paused: false });
  const state = await f.invoke({ action: "state" });
  assert.equal(state.selected, mine);
  const shown = state.tabs.find((entry) => entry.id === tab.id);
  assert.equal(shown.owner, "agent:chat-1");
  assert.deepEqual(shown.agent, { name: "Ada", kind: "heinzel" });
  // The panel was never laid out: the tab still has a size, and stays hidden.
  assert(f.views[1].sent.some(([name, bounds]) => name === "bounds" && bounds.width > 0));
  assert(f.views[1].sent.filter(([name]) => name === "visible").every(([, visible]) => visible === false));
  // An agent cannot pass for the person, whatever it is called.
  await f.refused(f.call({ id: "person", name: "x", kind: "nobody" }, "tabs"), "invalid");
  await f.refused(f.call({ id: "a b", name: "x", kind: "heinzel" }, "tabs"), "invalid");
  await f.refused(f.call({ ...HEINZEL, extra: 1 }, "tabs"), "invalid");
  assert.deepEqual((await f.call(HEINZEL, "tabs")).tabs.map((entry) => entry.id), [tab.id]);
});

test("limits: 4 tabs per agent, 16 per window with the person's 8 kept free", async () => {
  const f = fixture();
  for (let index = 0; index < 4; index += 1) await f.call(HEINZEL, "open");
  await f.refused(f.call(HEINZEL, "open"), "limit");
  for (let index = 0; index < 4; index += 1) await f.call(OTHER, "open");
  // Eight agent tabs are in the window: a third agent gets none, the person still gets all eight.
  await f.refused(f.call(OUTSIDE, "open"), "limit");
  for (let index = 0; index < 8; index += 1) await f.invoke({ action: "new-tab" });
  assert.equal((await f.invoke({ action: "state" })).tabs.length, 16);
  await assert.rejects(f.invoke({ action: "new-tab" }), /Too many tabs/);
});

test("ownership: the person's tab and another agent's tab do not exist for an agent", async () => {
  const f = fixture();
  await f.invoke({ action: "navigate", url: "https://bank.example.com" });
  const person = (await f.invoke({ action: "state" })).selected;
  const theirs = (await f.call(OTHER, "open", { url: "https://two.example.com" })).id;
  for (const tab of [person, theirs, "t-00000000"]) {
    for (const [command, args] of [["read", {}], ["text", {}], ["screenshot", {}], ["close", {}], ["navigate", { to: "reload" }],
      ["describe", { action: "key" }], ["type", { text: "x", expect: "0".repeat(64) }]])
      await f.refused(f.call(HEINZEL, command, { tab, ...args }), "unknown_tab");
  }
  assert.deepEqual((await f.call(HEINZEL, "tabs")).tabs, []);
  // Nothing was attached to or captured from those tabs, and the product window was never touched.
  assert.deepEqual(methods(f.views[0]).filter((name) => name === "attach" || name === "capture"), []);
  // The other agent's tab saw only its own opening: nothing was read, captured or typed there.
  assert.deepEqual(methods(f.views[1]).filter((name) => !["bounds", "visible", "attach", "DOM.enable", "Emulation.setDeviceMetricsOverride", "load"].includes(name)), []);
  assert.equal(f.productWire.attached, 0);
  assert.equal((await f.invoke({ action: "state" })).tabs.length, 2);
});

test("the debugger reaches guests of the tab table only, with listed methods and fixed scripts only", async () => {
  const f = fixture();
  const { tab, view } = await onPage(f);
  const guest = f.controller.agents.find(HEINZEL.id, tab);
  for (const method of ["Network.getCookies", "Network.getAllCookies", "Storage.getCookies", "Network.getResponseBody", "Target.createTarget",
    "Browser.close", "Page.navigate", "DOMStorage.getDOMStorageItems", "Fetch.enable", "Runtime.enable", "IO.read", "Page.printToPDF"]) {
    assert(!CDP_METHODS.includes(method));
    await assert.rejects(f.engine.protocol(HEINZEL.id, guest, method, {}), /not allowed/);
  }
  assert(CDP_METHODS.every((method) => !/^(?:Network|Storage|Target|Browser|Fetch|IO|DOMStorage|IndexedDB)\./.test(method)));
  // Script text never comes from outside this file.
  await assert.rejects(f.engine.protocol(HEINZEL.id, guest, "Runtime.evaluate", { expression: "document.cookie", contextId: 7 }), /not allowed/);
  await assert.rejects(f.engine.protocol(HEINZEL.id, guest, "Runtime.callFunctionOn", { functionDeclaration: "function(){return document.cookie}" }), /not allowed/);
  const before = view.sent.length;
  // Another agent's id, the product window and a made-up guest are no tab of the table.
  await f.refused(f.engine.protocol(OTHER.id, guest, "Accessibility.getFullAXTree"), "unknown_tab");
  await f.refused(f.engine.protocol(HEINZEL.id, { id: tab, owner: "agent:chat-1", agent: HEINZEL, view: { webContents: f.sender } }, "Accessibility.getFullAXTree"), "unknown_tab");
  assert.equal(view.sent.length, before);
  assert.equal(f.productWire.attached, 0);
  // The person's tab is never attached, whoever asks.
  await f.invoke({ action: "navigate", url: "https://mine.example.com" });
  assert.deepEqual(methods(f.views[1]).filter((name) => name === "attach"), []);
  assert.deepEqual(view.sent.filter(([name]) => name === "attach"), [["attach", "1.3"]]);
});

test("navigation: only public HTTPS, checked before a tab is made", async () => {
  const f = fixture();
  for (const url of ["http://example.com/", "https://localhost/", "https://127.0.0.1/", "http://127.0.0.1:8737/", "file:///etc/passwd",
    "https://user:pw@example.com/", "javascript:alert(1)", "https://intranet/", "https://printer.local/"])
    await f.refused(f.call(HEINZEL, "open", { url }), "refused_url");
  assert.equal(f.views.length, 0);
  const tab = (await f.call(HEINZEL, "open")).id;
  await f.refused(f.call(HEINZEL, "navigate", { tab, url: "https://10.0.0.1/" }), "refused_url");
  await f.refused(f.call(HEINZEL, "navigate", { tab, url: "https://a.example.com/", to: "back" }), "invalid");
  await f.refused(f.call(HEINZEL, "navigate", { tab }), "invalid");
  assert.equal((await f.call(HEINZEL, "navigate", { tab, url: "https://a.example.com/x" })).url, "https://a.example.com/x");
  await f.call(HEINZEL, "navigate", { tab, to: "reload" });
  assert(methods(f.views[0]).includes("reload"));
  // A page of an agent tab opens no popup, and a redirect to a local address is still stopped by the guest's own guard.
  // Not even right after input in the page, which opens a new tab from the person's own tab.
  f.views[0].webContents.emit("before-input-event", {}, { type: "keyDown" });
  assert.deepEqual(f.views[0].webContents.popup({ url: "https://other.example.com/" }), { action: "deny" });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(f.views.length, 1);
  let prevented = 0;
  f.views[0].webContents.emit("will-redirect", { url: "http://127.0.0.1:8737/api", preventDefault: () => { prevented += 1; } });
  assert.equal(prevented, 1);
});

test("read: refs come from the accessibility tree and die with the document", async () => {
  const f = fixture();
  const { tab, view } = await onPage(f);
  const read = await f.call(HEINZEL, "read", { tab });
  assert.equal(read.content, '- RootWebArea "Shop"\n  - button "More" [ref_1]\n  - textbox "Search" value="shoes" [ref_2]\n  - textbox "Password" [ref_3]');
  assert.equal(read.url, "https://shop.example.com/");
  assert.equal((await f.call(HEINZEL, "read", { tab, filter: "interactive", max_chars: 10 })).truncated, true);
  // What a field holds is shown for ordinary fields only (the password above is not), and for none on a page that takes a card.
  view.page.elements[6] = { ...FIELD, payment_page: true };
  assert.doesNotMatch((await f.call(HEINZEL, "read", { tab })).content, /value=/);
  view.page.elements[6] = FIELD;
  assert.equal((await f.call(HEINZEL, "read", { tab, ref: "ref_1" })).content, '- button "More" [ref_1]');
  assert.deepEqual(await f.call(HEINZEL, "text", { tab, max_chars: 5 }), { url: "https://shop.example.com/", title: "Title", content: "Hello", truncated: true });
  const described = await f.call(HEINZEL, "describe", { tab, action: "click", ref: "ref_1" });
  view.webContents.emit("did-navigate");
  await f.refused(f.call(HEINZEL, "describe", { tab, action: "click", ref: "ref_1" }), "stale_ref");
  await f.refused(f.call(HEINZEL, "click", { tab, ref: "ref_1", expect: described.fingerprint }), "stale_ref");
  await f.refused(f.call(HEINZEL, "read", { tab, ref: "ref_1" }), "stale_ref");
  await f.refused(f.call(HEINZEL, "scroll", { tab, ref: "ref_1" }), "stale_ref");
  // The new document's refs are new numbers, so an old ref never names a new element; the fingerprint is new as well.
  const fresh = (await f.call(HEINZEL, "read", { tab })).content;
  assert.match(fresh, /button "More" \[ref_4\]/);
  const again = await f.call(HEINZEL, "describe", { tab, action: "click", ref: "ref_4" });
  assert.deepEqual(again.target, described.target);
  assert.notEqual(again.fingerprint, described.fingerprint);
});

test("two-step input: the click runs only on the target that was described", async () => {
  const f = fixture();
  const { tab, view } = await onPage(f);
  const { target, fingerprint } = await f.call(HEINZEL, "describe", { tab, action: "click", ref: "ref_1" });
  assert.deepEqual(target, { resolved: true, host: "shop.example.com", role: "button", name: "More", tag: "button", type: "button",
    autocomplete: "", editable: false, submit: false, in_form: false, form_has_password: false, payment_page: false, href_host: null, never: null });
  assert.match(fingerprint, /^[0-9a-f]{64}$/);
  const inputs = () => view.sent.filter(([name]) => name.startsWith("Input."));
  await f.refused(f.call(HEINZEL, "click", { tab, ref: "ref_1" }), "invalid");
  await f.refused(f.call(HEINZEL, "click", { tab, ref: "ref_1", expect: "f".repeat(64) }), "changed");
  // The page renames the button after the gate saw it.
  view.page.elements[5] = { ...BUTTON, name: "Buy now", submit: true, in_form: true };
  await f.refused(f.call(HEINZEL, "click", { tab, ref: "ref_1", expect: fingerprint }), "changed");
  view.page.elements[5] = BUTTON;
  // Something else lies where the pointer would land.
  view.page.at = 77; view.page.elements[77] = { ...BUTTON, name: "Pay" }; view.page.covered = true;
  assert.deepEqual(inputs(), []);
  await f.refused(f.call(HEINZEL, "click", { tab, ref: "ref_1", expect: fingerprint }), "changed");
  // The pointer went there, nothing was pressed.
  assert.deepEqual(inputs().map(([, event]) => event.type), ["mouseMoved"]);
  view.page.covered = false;
  assert.deepEqual(await f.call(HEINZEL, "click", { tab, ref: "ref_1", count: 2, modifiers: ["shift"], expect: fingerprint }), { url: "https://shop.example.com/" });
  assert.deepEqual(inputs().slice(-5).map(([, event]) => [event.type, event.x, event.y, event.clickCount, event.modifiers]),
    [["mouseMoved", 20, 15, undefined, 8], ["mousePressed", 20, 15, 1, 8], ["mouseReleased", 20, 15, 1, 8], ["mousePressed", 20, 15, 2, 8], ["mouseReleased", 20, 15, 2, 8]]);
  // By coordinates: what is there is described; nothing there, or a frame, is not resolved.
  view.page.at = 5;
  const point = await f.call(HEINZEL, "describe", { tab, action: "click", x: 20, y: 15 });
  assert.equal(point.target.name, "More");
  view.page.at = null;
  const nothing = await f.call(HEINZEL, "describe", { tab, action: "click", x: 1, y: 1 });
  assert.equal(nothing.target.resolved, false);
  await f.refused(f.call(HEINZEL, "click", { tab, x: 1, y: 1, expect: point.fingerprint }), "changed");
  view.page.at = 8; view.page.elements[8] = { ...BUTTON, tag: "iframe", name: "" };
  const frame = await f.call(HEINZEL, "describe", { tab, action: "click", x: 5, y: 5 });
  assert.equal(frame.target.resolved, false);
  // Two controls that read the same are still two targets: a yes for one is no yes for the other, nor for another point.
  view.page.at = 77; view.page.elements[77] = BUTTON;
  const twin = await f.call(HEINZEL, "describe", { tab, action: "click", x: 20, y: 15 });
  assert.deepEqual(twin.target, point.target);
  assert.notEqual(twin.fingerprint, point.fingerprint);
  assert.notEqual((await f.call(HEINZEL, "describe", { tab, action: "click", x: 21, y: 15 })).fingerprint, twin.fingerprint);
  for (const args of [{ x: 1 }, { x: 1, y: 1, ref: "ref_1" }, { x: -1, y: 1 }, { x: 1.5, y: 1 }, { ref: "ref_0" }, { ref: "ref_1", extra: true }])
    await f.refused(f.call(HEINZEL, "describe", { tab, action: "click", ...args }), "invalid");
});

test("click race: a frame the page slides under the pointer is never clicked", async () => {
  const f = fixture();
  const { tab, view } = await onPage(f);
  view.page.elements[8] = { ...BUTTON, tag: "iframe", name: "" };
  const mouse = () => view.sent.filter(([name]) => name === "Input.dispatchMouseEvent").map(([, event]) => [event.type, event.x, event.y]);
  const { fingerprint } = await f.call(HEINZEL, "describe", { tab, action: "click", ref: "ref_1" });
  view.page.at = 5;
  // On mousemove the page puts another site's frame where the pointer is: seen before anything is pressed.
  view.page.on = (method, params) => {
    if (method === "Input.dispatchMouseEvent" && params.type === "mouseMoved") { view.page.at = 8; view.page.covered = true; }
  };
  await f.refused(f.call(HEINZEL, "click", { tab, ref: "ref_1", expect: fingerprint }), "changed");
  assert.deepEqual(mouse(), [["mouseMoved", 20, 15]]);
  // It waits for the press instead (a timer): the press lands in the frame, the recorder saw no press on the button,
  // and the button is let go outside the page, so the frame gets no click.
  view.sent.length = 0; view.page.at = 5; view.page.covered = false;
  view.page.on = (method, params) => {
    if (method === "Input.dispatchMouseEvent" && params.type === "mousePressed") view.page.pressed = false;
  };
  await assert.rejects(f.call(HEINZEL, "click", { tab, ref: "ref_1", expect: fingerprint }),
    (error) => error.code === "changed" && /put something else under the pointer\. The target was not clicked/.test(error.message));
  assert.deepEqual(mouse(), [["mouseMoved", 20, 15], ["mousePressed", 20, 15], ["mouseReleased", -1, -1]]);
  // The press landed on the button, but the page turned it into something else before the release.
  view.sent.length = 0; view.page.pressed = true;
  view.page.on = (method, params) => {
    if (method === "Input.dispatchMouseEvent" && params.type === "mousePressed") view.page.elements[5] = { ...BUTTON, name: "Pay now", submit: true };
  };
  await f.refused(f.call(HEINZEL, "click", { tab, ref: "ref_1", expect: fingerprint }), "changed");
  assert.deepEqual(mouse().at(-1), ["mouseReleased", -1, -1]);
  view.page.elements[5] = BUTTON;
  // The second press of a double click is checked like the first.
  view.sent.length = 0;
  let presses = 0;
  view.page.on = (method, params) => {
    if (method === "Input.dispatchMouseEvent" && params.type === "mousePressed" && ++presses === 2) view.page.pressed = false;
  };
  await f.refused(f.call(HEINZEL, "click", { tab, ref: "ref_1", count: 2, expect: fingerprint }), "changed");
  assert.deepEqual(mouse().map(([type, x]) => [type, x]), [["mouseMoved", 20], ["mousePressed", 20], ["mouseReleased", 20], ["mousePressed", 20], ["mouseReleased", -1]]);
  // By coordinates, after the person's yes to a frame there: a different frame under the point is not that target.
  view.sent.length = 0; view.page.pressed = true; view.page.on = () => {};
  view.page.at = 8;
  const frame = await f.call(HEINZEL, "describe", { tab, action: "click", x: 40, y: 40 });
  view.page.elements[88] = view.page.elements[8];
  view.page.on = (method, params) => {
    if (method === "Input.dispatchMouseEvent" && params.type === "mouseMoved") view.page.at = 88;
  };
  await f.refused(f.call(HEINZEL, "click", { tab, x: 40, y: 40, expect: frame.fingerprint }), "changed");
  assert.deepEqual(mouse(), [["mouseMoved", 40, 40]]);
  view.page.on = () => {}; view.page.at = 8;
  await f.call(HEINZEL, "click", { tab, x: 40, y: 40, expect: frame.fingerprint });
  assert.deepEqual(mouse().slice(-2), [["mousePressed", 40, 40], ["mouseReleased", 40, 40]]);
});

test("typing race: text and keys reach only the element whose focus was checked", async () => {
  const f = fixture();
  const { tab, view } = await onPage(f);
  const texts = () => view.sent.filter(([name]) => name === "Input.insertText").map(([, params]) => params.text.length);
  const scripts = (script) => view.sent.filter(([name, params]) => name.startsWith("Runtime.") && (params.expression ?? params.functionDeclaration) === script).length;
  view.page.onFocus = (node) => { view.page.focused = view.page.elements[node]; };
  const search = await f.call(HEINZEL, "describe", { tab, action: "type", ref: "ref_2" });
  // Long text goes in pieces, with the focus checked and the guard set before each and lifted after it.
  await f.call(HEINZEL, "type", { tab, ref: "ref_2", text: "x".repeat(450), expect: search.fingerprint });
  assert.deepEqual(texts(), [200, 200, 50]);
  assert.equal(scripts(PAGE_SCRIPTS.ARM_NODE), 3);
  assert.equal(scripts(PAGE_SCRIPTS.DISARM), 3);
  assert.equal(view.page.armed, null);
  // After the first piece the page moves the focus to its password field: no second piece.
  view.sent.length = 0;
  view.page.on = (method) => { if (method === "Input.insertText") view.page.focused = PASSWORD; };
  await f.refused(f.call(HEINZEL, "type", { tab, ref: "ref_2", text: "x".repeat(450), expect: search.fingerprint }), "changed");
  assert.deepEqual(texts(), [200]);
  assert.equal(view.page.armed, null);
  // The focus moved in the instant between the check and the text: the guard held the input back, and that is reported.
  view.sent.length = 0;
  view.page.on = (method) => { if (method === "Input.insertText") view.page.blocked = 1; };
  await assert.rejects(f.call(HEINZEL, "type", { tab, ref: "ref_2", text: "abc", expect: search.fingerprint }),
    (error) => error.code === "changed" && /moved the focus/.test(error.message));
  // Without a ref: the focused field is checked against what was described, before every piece.
  view.sent.length = 0; view.page.on = () => {}; view.page.focused = FIELD;
  const focused = await f.call(HEINZEL, "describe", { tab, action: "type" });
  view.page.on = (method) => { if (method === "Input.insertText") view.page.focused = { ...FIELD, hints: "other" , name: "Other" }; };
  await f.refused(f.call(HEINZEL, "type", { tab, text: "x".repeat(300), expect: focused.fingerprint }), "changed");
  assert.deepEqual(texts(), [200]);
  // A key: the same guard, and no key at all when the focus is elsewhere by then.
  view.sent.length = 0; view.page.on = () => {}; view.page.focused = FIELD;
  const enter = await f.call(HEINZEL, "describe", { tab, action: "key" });
  view.page.on = (method) => { if (method === "Input.dispatchKeyEvent") view.page.blocked = 1; };
  await f.refused(f.call(HEINZEL, "key", { tab, keys: "Enter", expect: enter.fingerprint }), "changed");
  view.sent.length = 0;
  // The focus moves right after the first look at it: the look that sets the guard sees the other field.
  let looked = false;
  view.page.blocked = 0;
  view.page.on = (method, params) => {
    if (method === "Runtime.evaluate" && params.expression === PAGE_SCRIPTS.FOCUSED) looked = true;
    else if (looked && method === "Page.getFrameTree") view.page.focused = PASSWORD;
  };
  await f.refused(f.call(HEINZEL, "key", { tab, keys: "Enter", expect: enter.fingerprint }), "changed");
  assert.deepEqual(view.sent.filter(([name]) => name === "Input.dispatchKeyEvent"), []);
  assert.equal(view.page.armed, null);
});

test("never: passwords, one-time codes and card fields are typed by the person only", async () => {
  const f = fixture();
  const { tab, view } = await onPage(f);
  const typed = () => view.sent.filter(([name]) => name === "Input.insertText" || name === "Input.dispatchKeyEvent");
  const secret = await f.call(HEINZEL, "describe", { tab, action: "type", ref: "ref_3" });
  assert.equal(secret.target.never, "password");
  // Refused on the description alone, before the field is even focused, whatever has the focus now.
  view.page.focused = FIELD;
  await f.refused(f.call(HEINZEL, "type", { tab, ref: "ref_3", text: "hunter2", expect: secret.fingerprint }), "never");
  assert(!methods(view).includes("DOM.focus"));
  // An ordinary field takes text...
  const search = await f.call(HEINZEL, "describe", { tab, action: "type", ref: "ref_2" });
  view.page.onFocus = (node) => { view.page.focused = view.page.elements[node]; };
  await f.call(HEINZEL, "type", { tab, ref: "ref_2", text: "blue shoes", expect: search.fingerprint });
  assert.deepEqual(typed().map(([, params]) => params.text), ["blue shoes"]);
  // ...unless the page moves the focus to a password field when it is focused.
  view.page.onFocus = () => { view.page.focused = PASSWORD; };
  await f.refused(f.call(HEINZEL, "type", { tab, ref: "ref_2", text: "again", expect: search.fingerprint }), "changed");
  // Without a ref the focused element is the target: a secret field, a frame or nothing takes no text.
  for (const [focused, never] of [[PASSWORD, "password"], [{ ...FIELD, autocomplete: "one-time-code" }, "one-time-code"],
    [{ ...FIELD, hints: "cardnumber" }, "payment-card"], [{ ...BUTTON, tag: "iframe" }, "frame"], [null, null]]) {
    view.page.focused = focused;
    const { target, fingerprint } = await f.call(HEINZEL, "describe", { tab, action: "type" });
    assert.equal(target.never, never);
    await f.refused(f.call(HEINZEL, "type", { tab, text: "1234", expect: fingerprint }), "never");
    const keys = await f.call(HEINZEL, "describe", { tab, action: "key" });
    if (focused) await f.refused(f.call(HEINZEL, "key", { tab, keys: "a", expect: keys.fingerprint }), "never");
  }
  assert.equal(typed().length, 1);
  // Leaving such a field with Tab is fine; Enter in an ordinary field reaches the page as a trusted key.
  view.page.focused = PASSWORD;
  const leave = await f.call(HEINZEL, "describe", { tab, action: "key" });
  await f.call(HEINZEL, "key", { tab, keys: "Tab", expect: leave.fingerprint });
  view.page.focused = FIELD;
  const enter = await f.call(HEINZEL, "describe", { tab, action: "key" });
  await f.call(HEINZEL, "key", { tab, keys: "Enter", expect: enter.fingerprint });
  assert.deepEqual(typed().slice(-2).map(([, params]) => [params.type, params.key, params.text]), [["keyDown", "Enter", "\r"], ["keyUp", "Enter", undefined]]);
  // The focus moved between the gate and the key.
  view.page.focused = PASSWORD;
  await f.refused(f.call(HEINZEL, "key", { tab, keys: "Enter", expect: enter.fingerprint }), "changed");
  for (const keys of ["ctrl+v", "meta+V", "cmd+c", "ctrl+x", "shift+Insert", "hyper+a", "ab", "ctrl+"])
    await f.refused(f.call(HEINZEL, "key", { tab, keys, expect: enter.fingerprint }), "invalid");
});

test("field and key rules as data", () => {
  assert.equal(neverField({ ...FIELD, autocomplete: "section-a cc-number" }), "payment-card");
  assert.equal(neverField({ ...FIELD, autocomplete: "new-password" }), "password");
  assert.equal(neverField({ ...FIELD, hints: "Kennwort" }), "password");
  assert.equal(neverField({ ...FIELD, hints: "otp-input numeric" }), "one-time-code");
  assert.equal(neverField({ ...FIELD, hints: "Verification code" }), "one-time-code");
  assert.equal(neverField({ ...FIELD, hints: "cvc" }), "payment-card");
  // What the page script found on the live page: a field drawn as dots, or one box of a split code.
  assert.equal(neverField({ ...FIELD, secret: "password" }), "password");
  assert.equal(neverField({ ...FIELD, secret: "one-time-code" }), "one-time-code");
  assert.equal(neverField({ ...BUTTON, secret: "password" }), null);
  assert.equal(neverField({ ...FIELD, secret: "anything-else" }), null);
  assert.equal(neverField({ ...FIELD, hints: "shipping option" }), null);
  assert.equal(neverField({ ...BUTTON, hints: "password" }), null);
  assert.equal(neverField(null), null);
  assert.equal(targetOf(null, null, "a.example.com").resolved, false);
  assert.equal(targetOf({ ...BUTTON, name: "Senden" }, { role: "button", name: "Send message" }, "a.example.com").name, "Send message / Senden");
  assert.equal(targetOf({ ...BUTTON, tag: "a", href: "pay.example.com" }, null, "a.example.com").href_host, "pay.example.com");
  assert.deepEqual(parseKeys("ctrl+a"), { key: "a", code: "KeyA", windowsVirtualKeyCode: 65, modifiers: 2 });
  assert.deepEqual(parseKeys("shift+a"), { key: "A", code: "KeyA", windowsVirtualKeyCode: 65, modifiers: 8, text: "A" });
  assert.deepEqual(parseKeys("F5"), { key: "F5", code: "F5", windowsVirtualKeyCode: 116, modifiers: 0 });
  assert.deepEqual(parseKeys("space"), { key: " ", code: "Space", windowsVirtualKeyCode: 32, modifiers: 0, text: " " });
  assert.equal(parseKeys("+").key, "+");
  assert.equal(parseKeys("ctrl++").key, "+");
  assert.equal(pngSize(Buffer.from("not a png at all, just text")), null);
  // A tree with ignored and generic nodes keeps their children, and a text that repeats its parent's name is dropped.
  const nodes = [
    { nodeId: "1", childIds: ["2"], role: { value: "RootWebArea" }, name: { value: "" } },
    { nodeId: "2", parentId: "1", childIds: ["3", "5"], role: { value: "generic" }, name: { value: "" } },
    { nodeId: "3", parentId: "2", childIds: ["4"], role: { value: "link" }, name: { value: "Home" }, backendDOMNodeId: 3 },
    { nodeId: "4", parentId: "3", role: { value: "StaticText" }, name: { value: "Home" }, backendDOMNodeId: 4 },
    { nodeId: "5", parentId: "2", ignored: true, childIds: ["6"], role: { value: "paragraph" } },
    { nodeId: "6", parentId: "5", role: { value: "StaticText" }, name: { value: "Welcome" }, backendDOMNodeId: 6 },
  ];
  assert.equal(outline(nodes, { refOf: (node) => `ref_${node}` }), '- link "Home" [ref_3]\n- text "Welcome"');
  assert.equal(outline(nodes, { filter: "interactive", refOf: (node) => `ref_${node}` }), '- link "Home" [ref_3]');
});

test("a tab the person is not looking at has a viewport of the panel's size, gone the moment it is shown", async () => {
  const f = fixture();
  const settle = () => new Promise((resolve) => setImmediate(resolve));
  // The person's own tab, so the panel can be laid out; then the agent's, never shown.
  await f.invoke({ action: "navigate", url: "https://mine.example.com" });
  const mine = (await f.invoke({ action: "state" })).selected;
  const tab = (await f.call(HEINZEL, "open", { url: "https://shop.example.com/" })).id;
  const view = f.views[1];
  const sizes = () => view.sent.filter(([name]) => name.startsWith("Emulation.")).map(([name, params]) => [name.split(".")[1], params.width, params.height]);
  // Sized before its first page loads, as a plain desktop page and with nothing but a width and a height.
  assert(methods(view).indexOf("Emulation.setDeviceMetricsOverride") < methods(view).indexOf("load"));
  assert.deepEqual(view.sent.find(([name]) => name === "Emulation.setDeviceMetricsOverride")[1], { width: 1280, height: 800, deviceScaleFactor: 0, mobile: false });
  // It stays from call to call, so the page keeps its layout and scroll position.
  await f.call(HEINZEL, "read", { tab });
  await f.call(HEINZEL, "scroll", { tab });
  assert.deepEqual(sizes(), [["setDeviceMetricsOverride", 1280, 800]]);
  // The panel is laid out: the hidden tab follows its size at the next call.
  await f.invoke({ action: "layout", bounds: { x: 0, y: 40, width: 400, height: 300 } });
  await f.call(HEINZEL, "text", { tab });
  assert.deepEqual(sizes().at(-1), ["setDeviceMetricsOverride", 400, 300]);
  // The person selects the agent's tab: the override goes at once, without any call of the agent.
  await f.invoke({ action: "select-tab", tab });
  await settle();
  assert.deepEqual(sizes().at(-1), ["clearDeviceMetricsOverride", undefined, undefined]);
  const shown = sizes().length;
  await f.call(HEINZEL, "read", { tab });
  await f.call(HEINZEL, "screenshot", { tab });
  assert.equal(sizes().length, shown);
  // No override while the person looks at it, and never another size or another kind of page.
  const guest = f.controller.agents.find(HEINZEL.id, tab);
  const fits = { width: 400, height: 300, deviceScaleFactor: 0, mobile: false };
  await assert.rejects(f.engine.protocol(HEINZEL.id, guest, "Emulation.setDeviceMetricsOverride", fits), /not allowed/);
  await f.invoke({ action: "select-tab", tab: mine });
  for (const params of [{ ...fits, width: 401 }, { ...fits, height: 8000 }, { ...fits, mobile: true }, { ...fits, deviceScaleFactor: 2 },
    { ...fits, screenWidth: 400 }, { ...fits, positionX: 0 }, { ...fits, viewport: { x: 0, y: 0, width: 400, height: 300, scale: 1 } }, { width: 400, height: 300 }])
    await assert.rejects(f.engine.protocol(HEINZEL.id, guest, "Emulation.setDeviceMetricsOverride", params), /not allowed/);
  await assert.rejects(f.engine.protocol(HEINZEL.id, guest, "Emulation.clearDeviceMetricsOverride", { extra: 1 }), /not allowed/);
  assert.equal(sizes().length, shown);
  // Hidden again: sized again at the next call. Take over and hand back each give the tab its own size back.
  await f.call(HEINZEL, "read", { tab });
  assert.deepEqual(sizes().at(-1), ["setDeviceMetricsOverride", 400, 300]);
  await f.invoke({ action: "take-over", tab });
  await settle();
  assert.deepEqual(sizes().at(-1), ["clearDeviceMetricsOverride", undefined, undefined]);
  await f.invoke({ action: "hand-back", tab });
  await f.call(HEINZEL, "read", { tab });
  assert.deepEqual(sizes().at(-1), ["setDeviceMetricsOverride", 400, 300]);
  await f.invoke({ action: "take-over", tab });
  await f.invoke({ action: "hand-back", tab });
  await settle();
  assert.deepEqual(sizes().slice(-2).map(([name]) => name), ["setDeviceMetricsOverride", "clearDeviceMetricsOverride"]);
  // The person's own tab never gets one, and closing the tab ends the session that set it.
  assert.deepEqual(methods(f.views[0]).filter((name) => name.startsWith("Emulation.") || name === "attach"), []);
  await f.call(HEINZEL, "read", { tab });
  await f.call(HEINZEL, "close", { tab });
  assert.equal(methods(view).at(-1), "detach");
});

test("screenshot: the guest view only, hidden or not, 1568 pixels at most", async () => {
  const f = fixture();
  const { tab, view } = await onPage(f);
  assert.deepEqual(await f.call(HEINZEL, "screenshot", { tab }), { url: "https://shop.example.com/", width: 800, height: 600, data: png(800, 600).toString("base64") });
  assert.deepEqual(view.sent.find(([name]) => name === "capture"), ["capture", { stayHidden: true }]);
  const half = await f.call(HEINZEL, "screenshot", { tab, scale: 0.5 });
  assert.deepEqual([half.width, half.height], [400, 300]);
  view.page.shot = image(3136, 1000);
  const wide = await f.call(HEINZEL, "screenshot", { tab });
  assert.deepEqual([wide.width, wide.height], [1568, 500]);
  // A view that never painted: the renderer is asked, and a dense display is scaled down again.
  view.page.shot = image(0, 0); view.page.dense = 3;
  const painted = await f.call(HEINZEL, "screenshot", { tab });
  assert(Math.max(painted.width, painted.height) <= 1568);
  assert.equal(view.sent.filter(([name]) => name === "Page.captureScreenshot").length, 2);
  await f.refused(f.call(HEINZEL, "screenshot", { tab, scale: 2 }), "invalid");
  assert.equal(f.productWire.attached, 0);
});

test("scroll moves the view with a wheel event", async () => {
  const f = fixture();
  const { tab, view } = await onPage(f);
  await f.call(HEINZEL, "scroll", { tab });
  await f.call(HEINZEL, "scroll", { tab, ref: "ref_1", direction: "up", amount: 2 });
  await f.call(HEINZEL, "scroll", { tab, x: 3, y: 4, direction: "right", amount: 1 });
  assert.deepEqual(view.sent.filter(([name]) => name === "Input.dispatchMouseEvent").map(([, event]) => [event.type, event.x, event.y, event.deltaX, event.deltaY]),
    [["mouseWheel", 400, 300, 0, 300], ["mouseWheel", 20, 15, 0, -200], ["mouseWheel", 3, 4, 100, 0]]);
  await f.refused(f.call(HEINZEL, "scroll", { tab, amount: 21 }), "invalid");
});

test("take over pauses one tab, hand back resumes it, stop closes the agent's tabs until it is allowed again", async () => {
  const f = fixture();
  const { tab } = await onPage(f);
  const second = (await f.call(HEINZEL, "open")).id;
  const other = (await f.call(OTHER, "open")).id;
  assert.equal((await f.invoke({ action: "take-over", tab })).tabs.find((entry) => entry.id === tab).paused, true);
  for (const [command, args] of [["read", {}], ["close", {}], ["navigate", { to: "reload" }], ["describe", { action: "key" }], ["screenshot", {}]])
    await f.refused(f.call(HEINZEL, command, { tab, ...args }), "paused");
  await assert.rejects(f.call(HEINZEL, "read", { tab }), /The person took over this tab\./);
  // Its other tab and the other agent go on.
  await f.call(HEINZEL, "text", { tab: second });
  assert.deepEqual((await f.call(HEINZEL, "tabs")).tabs.map((entry) => entry.paused), [true, false]);
  await f.invoke({ action: "hand-back", tab });
  await f.call(HEINZEL, "read", { tab });
  // The person steering an agent's tab from the address bar takes it over as well.
  await f.invoke({ action: "reload", tab });
  await f.refused(f.call(HEINZEL, "read", { tab }), "paused");
  await f.invoke({ action: "hand-back", tab });
  // Stop.
  const state = await f.invoke({ action: "stop-agent", tab });
  assert.deepEqual(state.tabs.map((entry) => entry.id), [other]);
  for (const command of ["tabs", "open"]) await f.refused(f.call(HEINZEL, command), "stopped");
  await f.refused(f.call(HEINZEL, "read", { tab }), "stopped");
  await f.call(OTHER, "text", { tab: other });
  await f.call(HEINZEL, "allow");
  assert.deepEqual((await f.call(HEINZEL, "tabs")).tabs, []);
  await f.call(HEINZEL, "open");
  assert.deepEqual(f.events.map((event) => [event.event, event.agent, event.tab === null ? null : event.tab === tab ? "tab" : "second"]), [
    ["take-over", "chat-1", "tab"], ["hand-back", "chat-1", "tab"], ["take-over", "chat-1", "tab"], ["hand-back", "chat-1", "tab"],
    ["stop", "chat-1", null], ["closed", "chat-1", "tab"], ["closed", "chat-1", "second"]]);
  // These controls exist for agent tabs only, and only with a tab id.
  await f.invoke({ action: "navigate", url: "https://mine.example.com" });
  const mine = (await f.invoke({ action: "state" })).selected;
  for (const action of ["take-over", "hand-back", "stop-agent", "commit-approve"]) {
    await assert.rejects(f.invoke({ action, tab: mine }), /Unknown tab/);
    await assert.rejects(f.invoke({ action }), /Invalid browser command/);
  }
});

test("an outside agent's commit waits for the person's answer on the tab", async (t) => {
  const f = fixture();
  const tab = (await f.call(OUTSIDE, "open", { url: "https://shop.example.com/" })).id;
  const ask = (seconds = 120) => f.call(OUTSIDE, "confirm", { tab, summary: "Click “Send”", fingerprint: "a".repeat(64), seconds });
  const settle = () => new Promise((resolve) => setImmediate(resolve));
  let pending = ask();
  await settle();
  assert.deepEqual((await f.invoke({ action: "state" })).tabs[0].commit, { summary: "Click “Send”" });
  await f.invoke({ action: "commit-approve", tab });
  assert.deepEqual(await pending, { approved: true });
  assert.equal((await f.invoke({ action: "state" })).tabs[0].commit, null);
  await assert.rejects(f.invoke({ action: "commit-approve", tab }), /Unknown tab/);
  pending = ask(); await settle();
  await f.invoke({ action: "commit-refuse", tab });
  assert.deepEqual(await pending, { approved: false });
  // No answer in time is a no.
  t.mock.timers.enable({ apis: ["setTimeout"] });
  pending = ask(30); await settle();
  t.mock.timers.tick(30000);
  assert.deepEqual(await pending, { approved: false });
  t.mock.timers.reset();
  // Closing the tab is a no; a Heinzel's commit never uses the tab card.
  pending = ask(); await settle();
  await f.invoke({ action: "close-tab", tab });
  assert.deepEqual(await pending, { approved: false });
  const own = (await f.call(HEINZEL, "open")).id;
  await f.refused(f.call(HEINZEL, "confirm", { tab: own, summary: "x", fingerprint: "a".repeat(64), seconds: 5 }), "invalid");
});

test("backstop limits per agent, and the idle close", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const f = fixture();
  const tab = (await f.call(HEINZEL, "open", { url: "https://a.example.com/" })).id;
  for (let index = 1; index < 20; index += 1) await f.call(HEINZEL, "navigate", { tab, url: "https://a.example.com/" });
  await assert.rejects(f.call(HEINZEL, "navigate", { tab, url: "https://a.example.com/" }), (error) => error.code === "rate" && /Try again in \d+ seconds/.test(error.message));
  for (let index = 20; index < 240; index += 1) await f.call(HEINZEL, "tabs");
  await f.refused(f.call(HEINZEL, "tabs"), "rate");
  await f.call(OTHER, "tabs");
  f.tick(61000);
  await f.call(HEINZEL, "tabs");
  // Thirty minutes without a call close the tab; a call starts the time again.
  t.mock.timers.tick(AGENT_IDLE_MS - 1000);
  await f.call(HEINZEL, "text", { tab });
  t.mock.timers.tick(AGENT_IDLE_MS - 1000);
  assert.equal((await f.call(HEINZEL, "tabs")).tabs.length, 1);
  t.mock.timers.tick(1000);
  assert.deepEqual((await f.call(HEINZEL, "tabs")).tabs, []);
  assert.deepEqual(f.events.at(-1), { event: "closed", agent: "chat-1", tab });
  assert(methods(f.views[0]).includes("detach"));
});

test("closing the product window closes every agent tab", async () => {
  const f = fixture();
  await f.call(HEINZEL, "open");
  await f.call(OTHER, "open");
  f.owner.emit("closed");
  assert.deepEqual((await f.call(HEINZEL, "tabs")).tabs, []);
  assert.deepEqual(f.events.map((event) => event.event), ["closed", "closed"]);
  f.controller.closeAll();
  await f.refused(f.call(HEINZEL, "open"), "failed");
});
