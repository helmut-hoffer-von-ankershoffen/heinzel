// Test-only bootstrap of the browser journey (ADR 0162): the real app, never on anyone's screen, with its guest
// partition sent to the journey's fixture instead of the network. Everything else is the product's own code.
import { app, BrowserWindow, dialog, session } from "electron";
import { appendFileSync, readFileSync } from "node:fs";
import { join } from "node:path";

const proxyPort = Number(process.env.HZ_JOURNEY_PROXY);
const hosts = new Set(String(process.env.HZ_JOURNEY_HOSTS ?? "").split(",").filter(Boolean));
if (!Number.isSafeInteger(proxyPort) || proxyPort < 1 || !hosts.size || !process.env.HZ_JOURNEY_CERT)
  throw new Error("The browser journey's fixture is not configured.");
const pem = (text) => String(text).replace(/\s+/g, "");
const trusted = pem(readFileSync(process.env.HZ_JOURNEY_CERT, "utf8"));

const root = process.env.HZ_NATIVE_TEST_ROOT;
if (!root) throw new Error("The isolated native test directory is required.");
app.setPath("crashDumps", join(root, "electron-crashes"));
const record = (file, line) => appendFileSync(join(root, file), `${line}\n`, { mode: 0o600 });
// No sheet may appear: a native question is recorded and answered with its cancel button; the journey expects none.
dialog.showMessageBox = async (...args) => {
  const options = args.at(-1) ?? {};
  record("dialogs", String(options.message ?? "dialog").slice(0, 120));
  return { response: Number.isInteger(options.cancelId) ? options.cancelId : 0, checkboxChecked: false };
};

// Diagnostic: an error box is never shown here (the shared bootstrap records its title); its text is kept as well,
// so a main-process error at quit says what it was. Anything after a # is cut: an address may end in the session's token.
let errorBox = () => {};
Object.defineProperty(dialog, "showErrorBox", {
  configurable: true,
  get: () => (title, content) => { record("main-errors", `${title}: ${String(content).replace(/#\S*/g, "#…").slice(0, 4000)}`); return errorBox(title, content); },
  set: (handler) => { errorBox = handler; },
});

// Hidden: no window is shown, focused or raised, and macOS gets no Dock icon and no activation. The product shows a
// guest only in a focused window, so the never-shown window says it is focused: the journey's person can then select
// the agent's tab in the panel, which gives that tab's view its size, with nothing on any screen.
// Only on Linux, and only when the journey says its display is a virtual one (Xvfb), is the window really shown:
// a shown window is what lets the compositor route a press into a frame of another site's process.
const shown = process.platform === "linux" && process.env.HZ_JOURNEY_VISIBLE === "1";
if (!shown) for (const name of ["show", "showInactive", "focus", "moveTop"]) BrowserWindow.prototype[name] = function hidden() {};
BrowserWindow.prototype.isFocused = function focused() { return true; };
if (process.platform === "darwin") {
  app.setActivationPolicy("accessory");
  app.dock?.hide();
}

// The guest partition, and only it: its proxy is the fixture's (which serves two made-up public names from loopback
// and refuses every other host), and the fixture's one certificate is trusted for those two names.
const fromPartition = session.fromPartition.bind(session);
const prepared = new WeakSet();
session.fromPartition = (partition, options) => {
  const guest = fromPartition(partition, options);
  if (partition !== "persist:hz-browser" || prepared.has(guest)) return guest;
  prepared.add(guest);
  const setProxy = guest.setProxy.bind(guest);
  const fixture = () => setProxy({ mode: "fixed_servers", proxyRules: `http://127.0.0.1:${proxyPort}`, proxyBypassRules: "<-loopback>" });
  // From the partition's first moment, and again whenever the product sets its own proxy; each product call is
  // recorded, so the journey fails if the product stopped configuring its guests' network.
  void fixture();
  guest.setProxy = () => { record("proxy-calls", "set"); return fixture(); };
  guest.setCertificateVerifyProc((request, callback) =>
    callback(hosts.has(request.hostname) && pem(request.certificate?.data) === trusted ? 0 : -3));
  return guest;
};

// The journey's seam, test-only: it makes the one case certain that the page's own timing wins only by chance, a
// press that lands inside a frame shown after the engine's last look. Just before the engine's own press goes to a
// guest, the fixture page is asked whether that point is its to cover; if so the page shows its frame and waits until
// it is drawn. Then the engine's press is passed on exactly as it came. Nothing else is touched: no command is
// changed, dropped or added, and every use is recorded.
app.on("web-contents-created", (_event, contents) => {
  if (!prepared.has(contents.session)) return;
  const wire = contents.debugger;
  const send = wire.sendCommand.bind(wire);
  wire.sendCommand = async (method, params, ...rest) => {
    if (method === "Input.dispatchMouseEvent" && params?.type === "mousePressed" && Number.isFinite(params.x) && Number.isFinite(params.y)) {
      // Two seconds at most: a page that is just leaving must not hold the engine's press.
      const used = await Promise.race([
        contents.executeJavaScript(`typeof window.__journeySeam === "function" ? window.__journeySeam(${params.x}, ${params.y}) : null`).catch(() => null),
        new Promise((done) => { setTimeout(done, 2000, null).unref?.(); }),
      ]);
      if (used) record("seam", JSON.stringify(used));
    }
    return send(method, params, ...rest);
  };
});

await import("./real-backend-bootstrap.mjs");
