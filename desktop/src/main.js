import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { app, BrowserWindow, dialog, ipcMain } from "electron";
import { lifecycle } from "./connection.js";
import { createInstanceLifecycle } from "./instance-lifecycle.js";
import { createOwnedCli } from "./owned-cli.js";

app.setName("Heinzelmännchen");
const instance = createInstanceLifecycle({
  app,
  parseConnection: () => null,
  focusWindow: (window) => {
    if (!window.isVisible()) window.show();
    window.focus();
  },
  createWindow: async () => {
    const window = new BrowserWindow({
      width: 1100,
      height: 760,
      show: false,
      webPreferences: {
        preload: fileURLToPath(new URL("./preload.cjs", import.meta.url)),
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true,
        webviewTag: false,
      },
    });
    window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
    window.webContents.on("will-navigate", (event) => event.preventDefault());
    window.on("closed", () => { if (!quitting) app.quit(); });
    const html = await readFile(new URL("./index.html", import.meta.url), "utf8");
    await window.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(html)}`);
    window.show();
    return window;
  },
});

let quitting = false;
const ownedCli = createOwnedCli();
if (instance.acquire(process.argv.slice(app.isPackaged ? 1 : 2))) {
  ipcMain.handle("hz:lifecycle", async (event) => {
    if (!BrowserWindow.fromWebContents(event.sender)) throw new Error("Unknown window.");
    return lifecycle(instance.currentConnection());
  });
  let stopping = false;
  let serviceStarted = false;
  void ownedCli.exited().then(() => {
    if (serviceStarted && !stopping && !quitting) {
      dialog.showErrorBox("Heinzelmännchen stopped", "The local service stopped. Reopen the app to start it again.");
      app.quit();
    }
  });
  app.on("before-quit", (event) => {
    if (quitting) return;
    event.preventDefault();
    if (stopping) return;
    stopping = true;
    void ownedCli.stop().then(() => {
      quitting = true;
      app.quit();
    }).catch(() => {
      stopping = false;
      dialog.showErrorBox(
        "Heinzelmännchen is still running",
        "The local service did not stop. The app will stay open so you can try Quit again.",
      );
    });
  });
  void app.whenReady().then(() => {
    if (stopping) return;
    return ownedCli.start().then((connection) => {
      if (stopping) return;
      if (!ownedCli.isRunning()) throw new Error("The local service stopped during startup.");
      instance.setConnection(connection);
      instance.setReady();
      serviceStarted = true;
      return instance.open();
    });
  }).catch((error) => {
    if (stopping) return;
    const message = error instanceof Error && error.message.includes("too long")
      ? "The local service did not become ready within 30 seconds. Check that the installed hz command works, then try again."
      : "The local service could not start. Check that Heinzelmännchen is installed and try again.";
    dialog.showErrorBox("Heinzelmännchen could not open", message);
    app.quit();
  });
}
