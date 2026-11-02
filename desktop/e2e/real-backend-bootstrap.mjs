// Test-only bootstrap: isolate Electron before its single-instance lock and observe only owned child metadata.
import { app, dialog } from "electron";
import childProcess from "node:child_process";
import { appendFileSync, writeFileSync } from "node:fs";
import { syncBuiltinESMExports } from "node:module";
import { join } from "node:path";

const root = process.env.HZ_NATIVE_TEST_ROOT;
if (!root) throw new Error("The isolated native test directory is required.");
app.setPath("userData", join(root, "electron-profile"));
app.setPath("sessionData", join(root, "electron-session"));
app.setPath("logs", join(root, "electron-logs"));
// Initial navigation can deliberately terminate Electron before Playwright can query it.
writeFileSync(join(root, "electron-paths"), JSON.stringify({
  userData: app.getPath("userData"),
  sessionData: app.getPath("sessionData"),
}), { mode: 0o600 });
dialog.showErrorBox = (title) => writeFileSync(join(root, "error-title"), title, { mode: 0o600 });

// What the app did and when, with no URL or token, so a launch that never exits says where it stopped.
const phase = (text) => appendFileSync(join(root, "phases"), `${Math.round(performance.now())} ${text}\n`, { mode: 0o600 });
for (const name of ["ready", "before-quit", "will-quit", "quit", "window-all-closed"]) app.on(name, () => phase(`app ${name}`));
app.on("child-process-gone", (_event, details) => phase(`app child-process-gone ${details.type} ${details.reason}`));
app.on("web-contents-created", (_event, contents) => {
  phase("page created");
  for (const name of ["did-start-loading", "will-redirect", "did-fail-load", "did-stop-loading", "did-finish-load", "destroyed"])
    contents.on(name, () => phase(`page ${name}`));
});

const spawn = childProcess.spawn;
childProcess.spawn = function observeOwned(command, args, options) {
  const child = spawn(command, args, options);
  if (command === "hz" && JSON.stringify(args) === JSON.stringify(["ui", "--port", "0", "--no-browser"])) {
    globalThis.ownedTestChild = child;
    writeFileSync(join(root, "owned-pid"), String(child.pid), { mode: 0o600 });
    phase("service started");
    child.once("exit", (code, signal) => phase(`service exit ${code} ${signal}`));
    child.once("exit", (code, signal) => writeFileSync(join(root, "owned-exit"), JSON.stringify({ code, signal }), { mode: 0o600 }));
    let pending = "";
    child.stdout.on("data", (chunk) => {
      pending += chunk.toString("utf8");
      if (pending.length > 4096) { pending = ""; return; }
      const newline = pending.indexOf("\n");
      if (newline < 0) return;
      try {
        const address = new URL(pending.slice(0, newline).trim());
        if (address.protocol === "http:" && address.hostname === "127.0.0.1" && address.port)
          writeFileSync(join(root, "owned-port"), address.port, { mode: 0o600 });
      } catch {
        // Malformed startup output remains the product's error to handle.
      } finally {
        pending = ""; // Do not persist or print the authentication fragment.
      }
    });
  }
  return child;
};
syncBuiltinESMExports();
if (process.env.HZ_NATIVE_TEST_CONTROLLER_GATE === "1") {
  const { waitForController } = await import("./controller-gate.mjs");
  void waitForController(root)
    .then(() => import("../src/main.js"))
    .catch((error) => setImmediate(() => { throw error; }));
} else {
  await import("../src/main.js");
}
