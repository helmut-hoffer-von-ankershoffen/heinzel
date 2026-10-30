import { fileURLToPath } from "node:url";
import { app, BrowserWindow, dialog, ipcMain } from "electron";
import { lifecycle } from "./connection.js";
import { createInstanceLifecycle } from "./instance-lifecycle.js";
import { createLifecycleHandler } from "./lifecycle-ipc.js";
import { createOwnedCli } from "./owned-cli.js";
import { initializeDesktopIdentity } from "./identity.js";
import { stopWithReport } from "./shutdown.js";
import { guardUiNavigation, openProductUi } from "./ui-navigation.js";

const identityReady = initializeDesktopIdentity(app, dialog);
const windows = new WeakSet();
let quitting = false;
let stopping = false;
const instance = createInstanceLifecycle({
  app,
  parseConnection: () => null,
  focusWindow: (window) => {
    if (!window.isVisible()) window.show();
    window.focus();
  },
  createWindow: (connection) => openProductUi({
    connection,
    lifecycle,
    cancelled: () => stopping || quitting,
    createWindow: () => new BrowserWindow({
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
    }),
    prepareWindow: (window) => {
      windows.add(window);
      // Native notification/capture consent is a separate lifecycle step, never implied by opening the UI.
      window.webContents.session.setPermissionCheckHandler(() => false);
      window.webContents.session.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
      guardUiNavigation(window.webContents, connection);
      window.on("closed", () => { if (!quitting) app.quit(); });
    },
  }),
});

const ownedCli = createOwnedCli();
if (identityReady && instance.acquire(process.argv.slice(app.isPackaged ? 1 : 2))) {
  ipcMain.handle("hz:lifecycle", createLifecycleHandler({
    windows,
    fromWebContents: (contents) => BrowserWindow.fromWebContents(contents),
    connection: () => instance.currentConnection(),
    lifecycle,
  }));
  let serviceStarted = false;
  let unexpectedExitReported = false;
  void ownedCli.exited().then(() => {
    if (serviceStarted && !stopping && !quitting) {
      unexpectedExitReported = true;
      dialog.showErrorBox(
        "Heinzelmännchen stopped unexpectedly",
        "The local service stopped unexpectedly. Check any work that was in progress, then reopen the app.",
      );
      app.quit();
    }
  });
  app.on("before-quit", (event) => {
    if (quitting) return;
    event.preventDefault();
    if (stopping) return;
    stopping = true;
    void stopWithReport(ownedCli, () => {
      if (unexpectedExitReported) return;
      dialog.showErrorBox(
        "Heinzelmännchen stopped unexpectedly",
        "The local service did not shut down cleanly. Check any work that was in progress before continuing.",
      );
    }).then(() => {
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
