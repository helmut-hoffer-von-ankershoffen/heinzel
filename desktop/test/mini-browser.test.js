import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test from "node:test";
import { BROWSER_PARTITION, createMiniBrowser, deviceDialog } from "../src/mini-browser.js";

function fixture(configure = async () => {}, extra = {}) {
  const frame = { processId: 1, frameToken: "main", url: "http://127.0.0.1:8737/", detached: false, isDestroyed: () => false };
  const sender = Object.assign(new EventEmitter(), { mainFrame: frame, isDestroyed: () => false, getZoomFactor: () => 1 });
  const views = [], sessions = [], options = [], calls = [];
  const owner = Object.assign(new EventEmitter(), { webContents: sender, isDestroyed: () => false, isFocused: () => true,
    getContentSize: () => [1000, 700], contentView: { addChildView: (view) => calls.push(["add", view]), removeChildView: () => calls.push(["remove"]) } });
  const controller = createMiniBrowser({ createNetwork: async () => ({ configure: async () => { await configure(); calls.push(["network-ready"]); }, close: () => calls.push(["network-close"]), authenticate: (_info, callback) => callback() }), windows: new WeakSet([owner]), fromWebContents: (contents) => contents === sender ? owner : null,
    connection: () => ({ origin: "http://127.0.0.1:8737", token: "a".repeat(32) }),
    createSession: (partition, config) => {
      const session = Object.assign(new EventEmitter(), { partition, config, clearStorageData: async () => calls.push(["clear"]),
        setPermissionCheckHandler: (handler) => { session.check = handler; },
        setPermissionRequestHandler: (handler) => { session.request = handler; },
        setDevicePermissionHandler: (handler) => { session.device = handler; },
        setBluetoothPairingHandler: (handler) => { session.pairing = handler; },
        setDisplayMediaRequestHandler: (handler) => { session.display = handler; },
        webRequest: { onBeforeRequest: (handler) => { session.before = handler; } } });
      sessions.push(session); return session;
    }, createView: (preferences) => {
      options.push(preferences); let url = "", dead = false;
      const contents = Object.assign(new EventEmitter(), { isDestroyed: () => dead, mainFrame: { top: null, origin: "", url: "" },
        close: (options) => { dead = true; calls.push(["close", options]); }, getURL: () => url,
        loadURL: async (target) => { url = target; calls.push(["load", target]); }, reload: () => calls.push(["reload"]),
        navigationHistory: { canGoBack: () => false, canGoForward: () => false },
        setWebRTCIPHandlingPolicy: (policy) => calls.push(["webrtc", policy]),
        setWindowOpenHandler: (handler) => { contents.popup = handler; } });
      const view = { webContents: contents, setBounds: (bounds) => calls.push(["bounds", bounds]), setVisible: (visible) => calls.push(["visible", visible]) };
      views.push(view); return view;
    }, ...extra });
  return { controller, owner, sender, frame, views, sessions, options, calls,
    invoke: (request) => controller.handle({ sender, senderFrame: frame }, request) };
}

test("native guest never borrows the product session/preload", async () => {
  const f = fixture(); await f.invoke({ action: "navigate", url: "https://example.com" });
  // One persistent website partition: never the product's default session, never per-window.
  assert.equal(f.sessions[0].partition, "persist:hz-browser");
  assert.equal(BROWSER_PARTITION, "persist:hz-browser");
  assert.equal(f.options[0].webPreferences.session, f.sessions[0]);
  assert.equal(f.options[0].webPreferences.preload, undefined);
  for (const key of ["nodeIntegration", "nodeIntegrationInSubFrames", "webviewTag"])
    assert.equal(f.options[0].webPreferences[key], false);
  assert.equal(f.options[0].webPreferences.sandbox, true);
  assert.equal(f.options[0].webPreferences.contextIsolation, true);
});
test("all permissions, downloads, popup dispatch and local redirects/subresources refuse", async () => {
  const f = fixture(); await f.invoke({ action: "navigate", url: "https://example.com" });
  const session = f.sessions[0], contents = f.views[0].webContents;
  for (const permission of ["media", "geolocation", "notifications", "display-capture", "clipboard-read", undefined])
    assert.equal(session.check(null, permission), false, permission);
  for (const permission of ["media", "display-capture", "geolocation", "hid", "usb", "serial"])
    session.request(null, permission, (allowed) => assert.equal(allowed, false));
  let prevented = 0;
  session.emit("will-download", { preventDefault: () => prevented++ });
  assert.deepEqual(contents.popup({ url: "https://other.example.com" }), { action: "deny" });
  contents.emit("will-redirect", { url: "http://127.0.0.1:8737", preventDefault: () => prevented++ });
  assert.equal(prevented, 2);
  for (const url of ["http://127.0.0.1:8737/api", "file:///private/data", "https://user:password@example.com"])
    session.before({ url }, (result) => assert.equal(result.cancel, true));
  session.before({ url: "https://example.com/image.png" }, (result) => assert.equal(result.cancel, false));
});
const settle = () => new Promise((resolve) => setImmediate(resolve));

test("device and security-key prompts show the person a chooser; only the picked device is granted", async () => {
  const asked = [];
  let answer = (devices) => devices[0].id;
  const f = fixture(undefined, { chooseDevice: async (request) => { asked.push(request); return answer(request.devices); } });
  await f.invoke({ action: "navigate", url: "https://keys.example.com/login" });
  const session = f.sessions[0], contents = f.views[0].webContents;
  const top = contents.mainFrame;
  Object.assign(top, { origin: "https://keys.example.com", url: "https://keys.example.com/login" });
  // An embed asks from its own frame: the chooser names the embed, not only the page around it.
  const embed = { top, origin: "https://embed.example.net", url: "https://embed.example.net/widget" };
  // Electron's handler shapes (DeviceInfoToValue), not the chooser entries.
  const key = { vendorId: 4176, productId: 1031, serialNumber: "KEY1" };
  assert.equal(session.device({ deviceType: "hid", origin: "https://keys.example.com", device: key }), false);
  for (const permission of ["hid", "usb", "serial"]) assert.equal(session.check(null, permission), true, permission);
  session.display({}, (result) => assert.deepEqual(result, {}));
  const lists = {
    "select-hid-device": [{ deviceList: [
      { deviceId: "h1", name: "Security Key", vendorId: 4176, productId: 1031, serialNumber: "KEY1" },
      { deviceId: "h2", name: "Keyboard", vendorId: 1452, productId: 641, serialNumber: "KBD" }], frame: top }],
    "select-usb-device": [{ deviceList: [{ deviceId: "u1", productName: "Token", manufacturerName: "Maker" }], frame: embed }],
    "select-serial-port": [[{ portId: "s1", displayName: "USB Serial", vendorId: "9025", productId: "67", serialNumber: "SER1" }], contents],
  };
  for (const [chooser, args] of Object.entries(lists)) {
    let prevented = false, picked = "unset";
    session.emit(chooser, { preventDefault: () => { prevented = true; } }, ...args, (id) => { picked = id; });
    await settle();
    assert.equal(prevented, true, chooser);
    assert.equal(picked, { "select-hid-device": "h1", "select-usb-device": "u1", "select-serial-port": "s1" }[chooser]);
  }
  assert.equal(asked.length, 3);
  assert.equal(asked[0].window, f.owner);
  assert.equal(asked[0].host, "keys.example.com");
  assert.equal(asked[1].host, "embed.example.net");
  assert.equal(asked[2].host, "keys.example.com");
  // Only the picked device, for the origin that asked, of that device type, is granted.
  const check = (deviceType, origin, device) => session.device({ deviceType, origin, device });
  assert.equal(check("hid", "https://keys.example.com", key), true);
  assert.equal(check("hid", "https://keys.example.com", { vendorId: 1452, productId: 641, serialNumber: "KBD" }), false);
  assert.equal(check("hid", "https://keys.example.com", { ...key, serialNumber: "OTHER" }), false);
  assert.equal(check("hid", "https://other.example.com", key), false);
  assert.equal(check("hid", "https://keys.example.com", { deviceId: "h1" }), false);
  assert.equal(check("usb", "https://keys.example.com", { deviceId: "u1" }), false);
  assert.equal(check("usb", "https://embed.example.net", { deviceId: "u1" }), true);
  assert.equal(check("serial", "https://keys.example.com", { vendor_id: 9025, product_id: 67, serial_number: "SER1", usb_driver: "x" }), true);
  assert.equal(check("serial", "https://keys.example.com", { vendor_id: 9025, product_id: 68, serial_number: "SER1" }), false);
  assert.equal(check("hid", "https://keys.example.com", { vendorId: 4176, productId: 1031 }), false);
  assert.equal(session.device(undefined), false);
  assert.deepEqual(asked[0].devices, [{ id: "h1", label: "Security Key" }, { id: "h2", label: "Keyboard" }]);
  assert.deepEqual(asked[1].devices, [{ id: "u1", label: "Token · Maker" }]);
  // Cancel, an unknown id or a failing dialog grant nothing; HID/USB cancel without an id, serial with "".
  for (const reply of [() => null, () => "not-offered", () => { throw new Error("dialog"); }]) {
    answer = reply;
    for (const [chooser, args] of Object.entries(lists)) {
      let picked = "unset";
      session.emit(chooser, { preventDefault: () => {} }, ...args, (id) => { picked = id; });
      await settle();
      assert.equal(picked, chooser === "select-serial-port" ? "" : undefined, chooser);
    }
  }
  // Bluetooth repeats while scanning: one chooser, the latest callback answers.
  answer = (devices) => devices.at(-1).id;
  const before = asked.length, answers = [];
  contents.emit("select-bluetooth-device", { preventDefault: () => {} }, [], (id) => answers.push(["first", id]));
  contents.emit("select-bluetooth-device", { preventDefault: () => {} }, [{ deviceId: "b1", deviceName: "Key" }], (id) => answers.push(["second", id]));
  contents.emit("select-bluetooth-device", { preventDefault: () => {} }, [{ deviceId: "b1", deviceName: "Key" }, { deviceId: "b2", deviceName: "Other" }], (id) => answers.push(["third", id]));
  await settle();
  assert.equal(asked.length, before + 1);
  assert.deepEqual(answers, [["third", "b1"]]);
  answer = () => "pair";
  const paired = [];
  session.pairing({ pairingKind: "confirm", frame: top }, (result) => paired.push(result));
  session.pairing({ pairingKind: "providePin", frame: top }, (result) => paired.push(result));
  answer = () => null;
  session.pairing({ pairingKind: "confirmPin", pin: "123456", frame: top }, (result) => paired.push(result));
  await settle();
  assert.deepEqual(paired, [{ confirmed: false }, { confirmed: true }, { confirmed: false }]);
  assert.equal(asked.at(-1).devices[0].label, "Pair with PIN 123456");
});

test("a device prompt with no device, or from a background window, says so instead of failing silently", async () => {
  const asked = [];
  const f = fixture(undefined, { chooseDevice: async (request) => { asked.push(request); return request.devices[0].id; } });
  await f.invoke({ action: "navigate", url: "https://keys.example.com/" });
  const session = f.sessions[0];
  let picked = "unset";
  const frame = f.views[0].webContents.mainFrame;
  session.emit("select-hid-device", { preventDefault: () => {} }, { deviceList: [], frame }, (id) => { picked = id; });
  await settle();
  assert.equal(picked, undefined);
  assert.equal((await f.invoke({ action: "state" })).notice, "no-device");
  f.sender.emit("did-start-navigation", { isMainFrame: false, isSameDocument: false });
  f.views[0].webContents.emit("did-start-navigation", { isMainFrame: true, isSameDocument: false });
  assert.equal((await f.invoke({ action: "state" })).notice, null);
  // A frame of no guest (another session user) never prompts.
  picked = "unset";
  session.emit("select-usb-device", { preventDefault: () => {} }, { deviceList: [{ deviceId: "u1" }], frame: { top: {} } }, (id) => { picked = id; });
  await settle();
  assert.equal(picked, undefined);
  f.owner.isFocused = () => false;
  session.emit("select-usb-device", { preventDefault: () => {} }, { deviceList: [{ deviceId: "u1" }], frame }, (id) => { picked = id; });
  await settle();
  assert.equal(picked, undefined);
  assert.equal(asked.length, 0);
});

test("the device dialog lists each device, then Cancel, and cancels by default", () => {
  const options = deviceDialog({ host: "keys.example.com", devices: [{ id: "a", label: "Key A" }, { id: "b", label: "Key B" }] });
  assert.deepEqual(options.buttons, ["Device: Key A", "Device: Key B", "Cancel"]);
  // A device that names itself "Cancel" stays recognisable as a device.
  assert.deepEqual(deviceDialog({ host: "x.example.com", devices: [{ id: "c", label: "Cancel" }] }).buttons, ["Device: Cancel", "Cancel"]);
  assert.equal(options.cancelId, 2);
  assert.equal(options.defaultId, 2);
  assert.match(options.message, /^keys\.example\.com wants to connect to a device$/);
  assert.match(deviceDialog({ host: "", devices: [] }).message, /^This website/);
});

test("a passkey step or a WebAuthn failure shows a visible note that passkeys may not work", async () => {
  const f = fixture();
  await f.invoke({ action: "navigate", url: "https://accounts.example.com/" });
  const contents = f.views[0].webContents;
  assert.equal((await f.invoke({ action: "state" })).notice, null);
  contents.emit("did-navigate", {}, "https://accounts.example.com/v3/signin/challenge/pk?x=1");
  assert.equal((await f.invoke({ action: "state" })).notice, "passkey");
  contents.emit("did-start-navigation", { isMainFrame: true, isSameDocument: false });
  contents.emit("did-navigate", {}, "https://accounts.example.com/v3/signin/challenge/pwd");
  assert.equal((await f.invoke({ action: "state" })).notice, null);
  contents.emit("console-message", { message: "Uncaught (in promise) NotAllowedError: The operation either timed out or was not allowed." });
  assert.equal((await f.invoke({ action: "state" })).notice, "passkey");
  contents.emit("did-start-navigation", { isMainFrame: true, isSameDocument: false });
  contents.emit("console-message", {}, 2, "WebAuthn request failed", 1, "x.js");
  assert.equal((await f.invoke({ action: "state" })).notice, "passkey");
  contents.emit("did-start-navigation", { isMainFrame: true, isSameDocument: false });
  contents.emit("console-message", { message: "ordinary log line" });
  contents.emit("did-navigate-in-page", {}, "not a url");
  assert.equal((await f.invoke({ action: "state" })).notice, null);
});

test("Open in browser hands only the guest's own public HTTPS location to the OS", async () => {
  const opened = [];
  const f = fixture(undefined, { openExternal: async (url, window) => { assert.equal(window, f.owner); opened.push(url); return true; } });
  assert.deepEqual(await f.invoke({ action: "external" }), { status: "empty", url: "", canGoBack: false, canGoForward: false, notice: null, tabs: [], selected: null });
  await f.invoke({ action: "navigate", url: "https://example.com/page?q=1" });
  await f.invoke({ action: "external" });
  assert.deepEqual(opened, ["https://example.com/page?q=1"]);
  await assert.rejects(f.invoke({ action: "external", url: "https://evil.example.com/" }), /Invalid browser command/);
  const contents = f.views[0].webContents;
  for (const url of ["", "http://example.com/", "file:///etc/hosts", "https://127.0.0.1/", "http://127.0.0.1:8737/"]) {
    contents.getURL = () => url;
    await assert.rejects(f.invoke({ action: "external" }));
  }
  assert.deepEqual(opened, ["https://example.com/page?q=1"]);
});

test("sign-ins persist across close and reopen; Clear browsing data empties the shared partition", async () => {
  const f = fixture();
  await f.invoke({ action: "navigate", url: "https://example.com" });
  await f.invoke({ action: "close" });
  await f.invoke({ action: "navigate", url: "https://example.com/again" });
  // Close never wipes: the reopened guest uses the same persistent partition, so its cookies are still there.
  assert.equal(f.sessions.length, 1);
  assert.equal(f.options[1].webPreferences.session, f.sessions[0]);
  assert.ok(!f.calls.some(([name]) => name === "clear"));
  const session = f.sessions[0];
  const cleared = [];
  session.clearCache = async () => cleared.push("cache");
  session.clearAuthCache = async () => cleared.push("auth");
  await f.invoke({ action: "clear" });
  assert.deepEqual(cleared.sort(), ["auth", "cache"]);
  // The page leaves its site before the wipe, so no running script writes data back; then it loads again.
  const order = f.calls.map(([name, target]) => (name === "load" ? target : name)).filter((name) => name !== "webrtc");
  const blank = order.lastIndexOf("about:blank"), wiped = order.lastIndexOf("clear");
  assert.ok(blank >= 0 && blank < wiped, order.join(" "));
  assert.equal(order.at(-1), "https://example.com/again");
  // A failed wipe is reported, not hidden.
  session.clearStorageData = async () => { throw new Error("disk"); };
  await assert.rejects(f.invoke({ action: "clear" }), /Browsing data could not be cleared/);
  // The restore navigation does not hide it; the next good clear does.
  f.views[1].webContents.emit("did-start-navigation", { isMainFrame: true, isSameDocument: false });
  assert.equal((await f.invoke({ action: "state" })).notice, "clear-failed");
  session.clearStorageData = async () => {};
  await f.invoke({ action: "clear" });
  assert.equal((await f.invoke({ action: "state" })).notice, null);
  // Clearing before any guest in this run still opens and empties the persisted partition.
  const cold = fixture();
  await cold.invoke({ action: "clear" });
  assert.equal(cold.sessions[0].partition, "persist:hz-browser");
  assert.ok(cold.calls.some(([name]) => name === "clear"));
  assert.equal(cold.views.length, 0);
});

test("one CONNECT broker serves the shared partition and ends with the last guest", async () => {
  const f = fixture();
  await f.invoke({ action: "navigate", url: "https://example.com" });
  await f.invoke({ action: "navigate", url: "https://example.com/two" });
  assert.equal(f.calls.filter(([name]) => name === "network-ready").length, 1);
  await f.invoke({ action: "close" });
  await settle();
  assert.equal(f.calls.filter(([name]) => name === "network-close").length, 1);
  await f.invoke({ action: "navigate", url: "https://example.com/three" });
  assert.equal(f.calls.filter(([name]) => name === "network-ready").length, 2);
});

test("every redirect hop and request to a non-public target is refused, a public hop passes", async () => {
  const f = fixture(); await f.invoke({ action: "navigate", url: "https://example.com" });
  const session = f.sessions[0], contents = f.views[0].webContents;
  const hops = ["http://example.com/", "https://127.0.0.1/", "https://10.0.0.5/", "https://192.168.1.2/",
    "https://172.20.0.1/", "https://169.254.169.254/", "https://100.101.102.103/", "https://mini.tail1234.ts.net/",
    "https://mini.local/", "https://localhost/", "file:///etc/hosts", "data:text/html,a", "javascript:alert(1)",
    "blob:https://example.com/id", "http://127.0.0.1:8737/"];
  for (const url of hops) {
    for (const event of ["will-redirect", "will-navigate", "will-frame-navigate"]) {
      let prevented = false;
      contents.emit(event, { url, preventDefault: () => { prevented = true; } });
      assert.equal(prevented, true, `${event}: ${url}`);
    }
    session.before({ url }, (result) => assert.equal(result.cancel, true, url));
  }
  let prevented = false;
  contents.emit("will-redirect", { url: "https://www.example.com/next", preventDefault: () => { prevented = true; } });
  assert.equal(prevented, false);
  await assert.rejects(f.invoke({ action: "navigate", url: "https://mini.tail1234.ts.net/" }));
});
test("surface layout is bounded, hidden on request, destroyed on product navigation, partition kept", async () => {
  const f = fixture(); await f.invoke({ action: "navigate", url: "https://example.com" });
  await assert.rejects(f.invoke({ action: "layout", bounds: { x: -1, y: 0, width: 100, height: 100 } }));
  await f.invoke({ action: "layout", bounds: { x: 600, y: 100, width: 300, height: 400 } });
  assert.deepEqual(f.calls.at(-1), ["visible", true]);
  f.owner.getContentSize = () => [800, 700];
  await assert.rejects(f.invoke({ action: "layout", bounds: { x: 600, y: 100, width: 300, height: 400 } }));
  assert.deepEqual(f.calls.at(-1), ["visible", false]);
  await f.invoke({ action: "layout", bounds: { x: 500, y: 100, width: 300, height: 400 } });
  assert.deepEqual(f.calls.at(-1), ["visible", true]);
  await f.invoke({ action: "hide" });
  assert.deepEqual(f.calls.at(-1), ["visible", false]);
  f.owner.emit("blur");
  assert.deepEqual(f.calls.at(-1), ["visible", false]);
  f.sender.emit("did-start-navigation", { isMainFrame: false, isSameDocument: false });
  assert.equal(f.views[0].webContents.isDestroyed(), false);
  f.sender.emit("did-start-navigation", { isMainFrame: true, isSameDocument: false });
  assert.ok(f.calls.some(([name, options]) => name === "close" && options.waitForBeforeUnload === false));
  assert.equal(f.sender.listenerCount("did-start-navigation"), 0);
  assert.equal(f.owner.listenerCount("closed"), 0);
  assert.ok(!f.calls.some(([name]) => name === "clear"));
  await f.invoke({ action: "navigate", url: "https://example.com" });
  // Reopening reuses the one persistent partition, with its handlers registered once.
  assert.equal(f.sessions.length, 1);
  assert.equal(f.sessions[0].listenerCount("will-download"), 1);
  f.controller.closeAll();
  assert.ok(f.views.every((view) => view.webContents.isDestroyed()));
});
test("unknown commands, fields and a guest sender cannot control the product", async () => {
  const f = fixture();
  for (const request of [{ action: "shell", url: "https://example.com" }, { action: "close", command: "x" }, null])
    await assert.rejects(f.invoke(request));
  await assert.rejects(f.controller.handle({ sender: { isDestroyed: () => false }, senderFrame: f.frame }, { action: "close" }));
  assert.equal(f.views.length, 0);
});
test("concurrent initial navigation creates only one guest while the guest opens", async () => {
  const f = fixture();
  await Promise.all([f.invoke({ action: "navigate", url: "https://example.com/one" }), f.invoke({ action: "navigate", url: "https://example.com/two" })]);
  assert.equal(f.views.length, 1);
});

test("guest declines platform certificates and authentication challenges", async () => {
  const f = fixture();
  await f.invoke({ action: "navigate", url: "https://example.com" });
  const contents = f.views[0].webContents;
  for (const [name, args] of [
    ["select-client-certificate", ["https://example.com", [{ fingerprint: "platform identity" }]]],
    ["login", [{}, {}]],
  ]) {
    let prevented = false;
    let declined = false;
    contents.emit(name, { preventDefault: () => { prevented = true; } }, ...args, (...values) => {
      assert.deepEqual(values, []);
      declined = true;
    });
    assert.ok(prevented && declined);
  }
  assert.ok(f.calls.some(([name, policy]) => name === "webrtc" && policy === "disable_non_proxied_udp"));
});

test("proxy setup fails closed and close during setup never loads a late website", async () => {
  const broken = fixture(async () => { throw new Error("private internal error"); });
  await assert.rejects(broken.invoke({ action: "navigate", url: "https://example.com" }), /^Error: Browser network is unavailable\.$/);
  assert.equal(broken.views.length, 0);
  assert(broken.calls.some(([name]) => name === "network-close"));
  let complete, started;
  const pending = new Promise(resolve => { complete = resolve; });
  const began = new Promise(resolve => { started = resolve; });
  const f = fixture(async () => { started(); await pending; });
  const navigation = f.invoke({ action: "navigate", url: "https://example.com" });
  await began;
  await f.invoke({ action: "close" });
  complete();
  await assert.rejects(navigation);
  assert(!f.calls.some(([name]) => name === "load"));
  assert(f.calls.some(([name]) => name === "network-close"));
  assert(f.views.every(view => view.webContents.isDestroyed()));
});

test("Clear browsing data also revokes every device the person picked", async () => {
  const f = fixture(undefined, { chooseDevice: async ({ devices }) => devices[0].id });
  await f.invoke({ action: "navigate", url: "https://keys.example.com/" });
  const session = f.sessions[0], frame = f.views[0].webContents.mainFrame;
  Object.assign(frame, { origin: "https://keys.example.com" });
  session.emit("select-hid-device", { preventDefault: () => {} },
    { deviceList: [{ deviceId: "h1", vendorId: 4176, productId: 1031, serialNumber: "KEY1" }], frame }, () => {});
  await settle();
  const granted = () => session.device({ deviceType: "hid", origin: "https://keys.example.com",
    device: { vendorId: 4176, productId: 1031, serialNumber: "KEY1" } });
  assert.equal(granted(), true);
  await f.invoke({ action: "clear" });
  assert.equal(granted(), false);
});

test("device identity matches Electron's chooser and handler shapes, and only with a serial number", async () => {
  const { deviceIdentity } = await import("../src/mini-browser.js");
  assert.equal(deviceIdentity("hid", { deviceId: "x", vendorId: 4176, productId: 1031, serialNumber: "A" }),
    deviceIdentity("hid", { vendorId: "0x1050", productId: "1031", serialNumber: "A" }));
  assert.equal(deviceIdentity("hid", { vendorId: 1, productId: 2, guid: "g" }), "hid:1:2:g");
  assert.equal(deviceIdentity("serial", { vendorId: "0x2341", productId: "0x0043", serialNumber: "S" }),
    deviceIdentity("serial", { vendor_id: 9025, product_id: 67, serial_number: "S" }));
  for (const [type, device] of [["hid", { vendorId: 1, productId: 2 }], ["serial", { vendor_id: 1, product_id: 2, guid: "g" }],
    ["hid", { vendorId: "zz", productId: 2, serialNumber: "s" }], ["usb", {}], ["bluetooth", { deviceId: "b" }], ["hid", null]])
    assert.equal(deviceIdentity(type, device), null, JSON.stringify([type, device]));
});

test("clear waits for a guest that is still opening before it wipes", async () => {
  let ready;
  const gate = new Promise((resolve) => { ready = resolve; });
  const f = fixture(() => gate);
  const navigation = f.invoke({ action: "navigate", url: "https://example.com/" });
  await settle();
  const clearing = f.invoke({ action: "clear" });
  await settle();
  assert.ok(!f.calls.some(([name]) => name === "clear"));
  ready();
  await navigation;
  await clearing;
  const order = f.calls.map(([name, target]) => (name === "load" ? target : name));
  assert.ok(order.indexOf("about:blank") < order.indexOf("clear"), order.join(" "));
});

const BOUNDS = { x: 0, y: 40, width: 400, height: 300 };

test("tabs: open, select and close; every tab shares the one partition and is tagged with its owner", async () => {
  const f = fixture();
  await f.invoke({ action: "navigate", url: "https://one.example.com" });
  const second = await f.invoke({ action: "new-tab", url: "https://two.example.com" });
  const blank = await f.invoke({ action: "new-tab" });
  assert.equal(blank.tabs.length, 3);
  assert.deepEqual(blank.tabs.map((tab) => tab.owner), ["person", "person", "person"]);
  assert(blank.tabs.every((tab) => /^t-[0-9a-f]{8}$/.test(tab.id)));
  assert.equal(blank.selected, blank.tabs[2].id);
  assert.equal(f.sessions.length, 1);
  assert(f.options.every((o) => o.webPreferences.session === f.sessions[0] && o.webPreferences.sandbox === true));
  assert.equal(second.tabs[1].url, "https://two.example.com/");
  const [a, b] = blank.tabs;
  // Only the selected tab with a page is shown.
  await f.invoke({ action: "layout", bounds: BOUNDS });
  assert.deepEqual(f.calls.filter((c) => c[0] === "visible").slice(-3).map((c) => c[1]), [false, false, false]);
  assert.equal((await f.invoke({ action: "select-tab", tab: a.id })).selected, a.id);
  assert.deepEqual(f.calls.filter((c) => c[0] === "visible").slice(-3).map((c) => c[1]), [true, false, false]);
  assert.equal((await f.invoke({ action: "state" })).url, "https://one.example.com/");
  assert.equal((await f.invoke({ action: "close-tab", tab: a.id })).selected, b.id);
  assert.equal(f.views[0].webContents.isDestroyed(), true);
  const last = (await f.invoke({ action: "close-tab", tab: blank.tabs[2].id }));
  assert.equal(last.selected, b.id);
  assert.equal((await f.invoke({ action: "close-tab", tab: b.id })).tabs.length, 0);
  assert.equal((await f.invoke({ action: "state" })).status, "empty");
});

test("tabs: ids are checked, the limit is 8, and bad tab commands are refused", async () => {
  const f = fixture();
  await f.invoke({ action: "navigate", url: "https://one.example.com" });
  for (const bad of [{ action: "select-tab" }, { action: "select-tab", tab: "t-00000000" }, { action: "close-tab", tab: "../x" },
    { action: "close-tab", tab: 5 }, { action: "state", tab: "nope" }, { action: "new-tab", tab: "t-00000000" },
    { action: "tabs", extra: 1 }, { action: "new-tab", url: "http://127.0.0.1:8737/" }, { action: "new-tab", url: "https://x.example.com:8443" }])
    await assert.rejects(f.invoke(bad), /./, JSON.stringify(bad));
  assert.equal((await f.invoke({ action: "tabs" })).tabs.length, 1);
  for (let i = 1; i < 8; i++) await f.invoke({ action: "new-tab" });
  await assert.rejects(f.invoke({ action: "new-tab" }), /Too many tabs/);
  assert.equal((await f.invoke({ action: "tabs" })).tabs.length, 8);
});

test("tabs: every ADR 0140 rule holds in each tab", async () => {
  const f = fixture();
  await f.invoke({ action: "navigate", url: "https://one.example.com" });
  await f.invoke({ action: "new-tab", url: "https://two.example.com" });
  for (const view of f.views) {
    const contents = view.webContents;
    let prevented = 0;
    contents.emit("will-redirect", { url: "http://127.0.0.1:8737", preventDefault: () => prevented++ });
    contents.emit("will-navigate", { url: "file:///etc/passwd", preventDefault: () => prevented++ });
    contents.emit("will-attach-webview", { preventDefault: () => prevented++ });
    assert.equal(prevented, 3);
    assert.deepEqual(contents.popup({ url: "https://x.example.com" }), { action: "deny" });
  }
  assert(f.calls.filter((c) => c[0] === "webrtc").length === 2);
});

test("tabs: Clear browsing data and closing the window cover every tab", async () => {
  const f = fixture();
  await f.invoke({ action: "navigate", url: "https://one.example.com" });
  await f.invoke({ action: "new-tab", url: "https://two.example.com" });
  await f.invoke({ action: "clear" });
  const loads = f.calls.filter((c) => c[0] === "load").map((c) => c[1]);
  assert.deepEqual(loads.filter((u) => u === "about:blank").length, 2);
  assert.equal(loads.filter((u) => u === "https://two.example.com/").length, 2);
  f.owner.emit("closed");
  assert(f.views.every((view) => view.webContents.isDestroyed()));
  await settle();
  assert.equal(f.calls.filter((c) => c[0] === "network-close").length, 1);
});

test("tabs: the product page navigating or the panel hiding keeps tabs; only a new document closes them", async () => {
  const f = fixture();
  await f.invoke({ action: "navigate", url: "https://one.example.com" });
  await f.invoke({ action: "new-tab", url: "https://two.example.com" });
  await f.invoke({ action: "hide" });
  assert.equal((await f.invoke({ action: "tabs" })).tabs.length, 2);
  f.sender.emit("did-start-navigation", { isMainFrame: true, isSameDocument: true });
  assert.equal((await f.invoke({ action: "tabs" })).tabs.length, 2);
  f.sender.emit("did-start-navigation", { isMainFrame: true, isSameDocument: false });
  assert(f.views.every((view) => view.webContents.isDestroyed()));
});

test("tabs: the limit counts the person's own tabs, and a foreign-owner tab does not use it", async () => {
  const f = fixture();
  await f.invoke({ action: "navigate", url: "https://one.example.com" });
  for (let i = 1; i < 8; i++) await f.invoke({ action: "new-tab" });
  await assert.rejects(f.invoke({ action: "new-tab" }), /Too many tabs/);
});

test("links: a link opens a new selected tab; a refused address or a full strip leaves a note with the link", async () => {
  const opened = [];
  const f = fixture(undefined, { openExternal: async (url) => { opened.push(url); return true; } });
  await f.invoke({ action: "navigate", url: "https://one.example.com" });
  const first = await f.controller.openLink(f.owner, "https://two.example.com/page");
  assert.equal(first.tabs.length, 2);
  assert.equal(first.selected, first.tabs[1].id);
  assert.equal(first.tabs[1].url, "https://two.example.com/page");
  assert.equal(first.link, null);
  const refused = await f.controller.openLink(f.owner, "http://example.com/plain");
  assert.deepEqual(refused.link, { url: "http://example.com/plain", reason: "refused", external: true });
  assert.equal(refused.tabs.length, 2);
  const local = await f.controller.openLink(f.owner, "https://localhost/x");
  assert.equal(local.link.reason, "refused");
  assert.equal(local.link.external, false);
  assert.equal((await f.invoke({ action: "link-external" })).link, null);
  assert.deepEqual(opened, []);
  for (let i = 2; i < 8; i++) await f.invoke({ action: "new-tab" });
  const full = await f.controller.openLink(f.owner, "https://late.example.com/");
  assert.deepEqual(full.link, { url: "https://late.example.com/", reason: "limit", external: true });
  assert.equal(full.tabs.length, 8);
  assert.equal((await f.invoke({ action: "link-external" })).link, null);
  assert.deepEqual(opened, ["https://late.example.com/"]);
  assert.equal((await f.controller.openLink(f.owner, "https://late.example.com/")).link.reason, "limit");
  assert.equal((await f.invoke({ action: "link-dismiss" })).link, null);
});

test("links: a page's target=_blank opens a tab only right after the person's input", async () => {
  const f = fixture();
  await f.invoke({ action: "navigate", url: "https://one.example.com" });
  const contents = f.views[0].webContents;
  assert.deepEqual(contents.popup({ url: "https://pop.example.com/" }), { action: "deny" });
  await settle();
  assert.equal((await f.invoke({ action: "tabs" })).tabs.length, 1);
  contents.emit("before-input-event", {}, { type: "mouseDown" });
  assert.deepEqual(contents.popup({ url: "https://pop.example.com/" }), { action: "deny" });
  await settle();
  const after = await f.invoke({ action: "tabs" });
  assert.equal(after.tabs.length, 2);
  assert.equal(after.tabs[1].url, "https://pop.example.com/");
  contents.emit("before-input-event", {}, { type: "mouseDown" });
  contents.popup({ url: "http://127.0.0.1:8737/" });
  await settle();
  assert.equal((await f.invoke({ action: "tabs" })).tabs.length, 2);
});
