// Test-only bootstrap of the browser journey (ADR 0162): the real app, never on anyone's screen, with its guest
// partition sent to the journey's fixture instead of the network. Everything else is the product's own code.
import { app, BrowserWindow, session } from "electron";
import { readFileSync } from "node:fs";

const proxyPort = Number(process.env.HZ_JOURNEY_PROXY);
const hosts = new Set(String(process.env.HZ_JOURNEY_HOSTS ?? "").split(",").filter(Boolean));
if (!Number.isSafeInteger(proxyPort) || proxyPort < 1 || !hosts.size || !process.env.HZ_JOURNEY_CERT)
  throw new Error("The browser journey's fixture is not configured.");
const pem = (text) => String(text).replace(/\s+/g, "");
const trusted = pem(readFileSync(process.env.HZ_JOURNEY_CERT, "utf8"));

// Hidden: no window is shown, focused or raised, and macOS gets no Dock icon and no activation.
for (const name of ["show", "showInactive", "focus", "moveTop"]) BrowserWindow.prototype[name] = function hidden() {};
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
  guest.setProxy = () => setProxy({ mode: "fixed_servers", proxyRules: `http://127.0.0.1:${proxyPort}`, proxyBypassRules: "<-loopback>" });
  guest.setCertificateVerifyProc((request, callback) =>
    callback(hosts.has(request.hostname) && pem(request.certificate?.data) === trusted ? 0 : -3));
  return guest;
};

await import("./real-backend-bootstrap.mjs");
