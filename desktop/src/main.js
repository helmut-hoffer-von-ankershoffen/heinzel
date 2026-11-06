import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { app, BrowserWindow, dialog, ipcMain } from "electron";
import { lifecycle } from "./connection.js";
import { createInstanceLifecycle } from "./instance-lifecycle.js";
import { createLifecycleHandler } from "./lifecycle-ipc.js";
import { acquireDesktopGate, createOwnedCli, installedCli } from "./owned-cli.js";
import { createNativePermissions } from "./native-permissions.js";
import { initializeDesktopIdentity } from "./identity.js";
import { refreshAfterQuit, shouldRefresh, stopWithReport } from "./shutdown.js";
import { installSignalQuit } from "./signal-quit.js";
import { guardUiNavigation, openProductUi } from "./ui-navigation.js";

const identityReady = initializeDesktopIdentity(app, dialog);
const windows = new WeakSet();
let quitting = false;
let stopping = false;
let permissions;
let desktopGate;
let gateReleasing = false;
const signalQuit = installSignalQuit({
  proc: process,
  quit: () => app.quit(),
  forceExit: (code) => app.exit(code),
});
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
      const windowPermissions = createNativePermissions({
        windows,
        fromWebContents: (contents) => BrowserWindow.fromWebContents(contents),
        connection: () => (stopping || quitting ? null : instance.currentConnection()),
        prompt: (owner, options) => dialog.showMessageBox(owner, options),
      });
      permissions = windowPermissions;
      window.webContents.session.setPermissionCheckHandler(windowPermissions.check);
      window.webContents.session.setPermissionRequestHandler(windowPermissions.request);
      window.webContents.on("did-start-navigation", windowPermissions.navigation);
      guardUiNavigation(window.webContents, connection);
      window.on("closed", () => { windowPermissions.revoke(); if (!quitting) app.quit(); });
    },
  }),
});

const ownedCli = createOwnedCli({ executable: app.isPackaged ? installedCli : () => "hz" });
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
    permissions?.revoke();
    let unclean = false;
    void stopWithReport(ownedCli, () => {
      unclean = true;
      if (signalQuit.received() || unexpectedExitReported) return;
      dialog.showErrorBox(
        "Heinzelmännchen stopped unexpectedly",
        "The local service did not shut down cleanly. Check any work that was in progress before continuing.",
      );
    }).then(async (result) => {
      const eligible = shouldRefresh({
        packaged: app.isPackaged, platform: process.platform, fixture: Boolean(process.env.HZ_NATIVE_TEST_ROOT),
        serviceStarted, unexpectedExit: unexpectedExitReported, signal: signalQuit.received(), outcome: result.outcome,
      });
      if (eligible && desktopGate) {
        gateReleasing = true;
        await refreshAfterQuit({ eligible, gate: desktopGate, executable: installedCli, spawn });
      }
      quitting = true;
      if (!signalQuit.exitIfUnclean(unclean)) app.quit();
    }).catch(() => {
      stopping = false;
      if (signalQuit.received()) {
        app.exit(1);
        return;
      }
      dialog.showErrorBox(
        "Heinzelmännchen is still running",
        "The local service did not stop. The app will stay open so you can try Quit again.",
      );
    });
  });
  void app.whenReady().then(() => {
    if (stopping) return;
    return (app.isPackaged ? acquireDesktopGate() : Promise.resolve(null)).then((gate) => {
      if (gate) {
        desktopGate = gate;
        gate.once("close", () => { if (!quitting && !gateReleasing) app.quit(); });
        gate.once("error", () => { if (!quitting && !gateReleasing) app.quit(); });
      }
      if (stopping) return null;
      return ownedCli.start();
    }).then((connection) => {
      if (stopping || !connection) return;
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
