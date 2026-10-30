// Test-only bootstrap: isolate Electron before its single-instance lock and observe only owned child metadata.
import { app, dialog } from "electron";
import childProcess from "node:child_process";
import { writeFileSync } from "node:fs";
import { syncBuiltinESMExports } from "node:module";
import { join } from "node:path";

const root = process.env.HZ_NATIVE_TEST_ROOT;
if (!root) throw new Error("The isolated native test directory is required.");
app.setPath("userData", join(root, "electron-profile"));
app.setPath("sessionData", join(root, "electron-session"));
app.setPath("logs", join(root, "electron-logs"));
dialog.showErrorBox = (title) => writeFileSync(join(root, "error-title"), title, { mode: 0o600 });

const spawn = childProcess.spawn;
childProcess.spawn = function observeOwned(command, args, options) {
  const child = spawn(command, args, options);
  if (command === "hz" && JSON.stringify(args) === JSON.stringify(["ui", "--port", "0", "--no-browser"])) {
    writeFileSync(join(root, "owned-pid"), String(child.pid), { mode: 0o600 });
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
await import("../src/main.js");
