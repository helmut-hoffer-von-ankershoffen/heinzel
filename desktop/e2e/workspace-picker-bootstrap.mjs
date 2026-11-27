// Isolated Electron journey, hidden on the host. Trust only the synthetic remote's loopback certificate.
import { app, BrowserWindow, safeStorage } from "electron";
import { readFileSync } from "node:fs";
const normalize = (pem) => pem.replace(/\s/g, "");
const leaf = normalize(readFileSync(process.env.HZ_TEST_CERT, "utf8"));
// Keep synthetic sign-ins session-only. An isolated HOME still has access to the login Keychain on macOS.
safeStorage.isEncryptionAvailable = () => false;
app.commandLine.appendSwitch("use-mock-keychain");
app.setActivationPolicy?.("accessory");
app.dock?.hide();
for (const name of ["show", "showInactive", "focus", "moveTop"]) BrowserWindow.prototype[name] = function hidden() {};
app.on("session-created", (partition) => {
  partition.setCertificateVerifyProc((request, done) => done(request.hostname === "127.0.0.1" &&
    normalize(request.certificate.data) === leaf ? 0 : -3));
});
await import("./real-backend-bootstrap.mjs");
