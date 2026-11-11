import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { app, BrowserWindow, dialog, ipcMain, session, shell, WebContentsView, webContents } from "electron";
import { lifecycle } from "./connection.js";
import { createInstanceLifecycle } from "./instance-lifecycle.js";
import { createLifecycleHandler } from "./lifecycle-ipc.js";
import { createMiniBrowser } from "./mini-browser.js";
import { acquireDesktopGate, createOwnedCli, installedCli } from "./owned-cli.js";
import { createNativePermissions } from "./native-permissions.js";
import { createPublicMediaHandler } from "./public-media.js";
import { createWorkspaceAudio } from "./workspace-audio.js";
import { initializeDesktopIdentity } from "./identity.js";
import { refreshAfterQuit, shouldRefresh, stopWithReport } from "./shutdown.js";
import { installSignalQuit } from "./signal-quit.js";
import { guardUiNavigation, openProductUi } from "./ui-navigation.js";
import { windowOptions } from "./window-options.js";

const identityReady = initializeDesktopIdentity(app, dialog);
const windows = new WeakSet();
let quitting = false;
let stopping = false;
let permissions;
let desktopGate;
let gateReleasing = false;
let workspaceAudio;
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
      ...windowOptions(process.platform),
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
      const audio = createWorkspaceAudio({
        windows,
        fromWebContents: (contents) => BrowserWindow.fromWebContents(contents),
        fromFrame: (frame) => frame ? webContents.fromFrame(frame) : null,
        connection: () => (stopping || quitting ? null : instance.currentConnection()),
        prompt: (owner, options) => dialog.showMessageBox(owner, options),
      });
      workspaceAudio = audio;
      window.webContents.session.setPermissionCheckHandler((contents, permission, origin, details) =>
        permission === "display-capture" ? audio.check(contents, permission, origin, details) :
          windowPermissions.check(contents, permission, origin, details));
      window.webContents.session.setPermissionRequestHandler((contents, permission, callback, details) =>
        permission === "display-capture" ? audio.request(contents, permission, callback, details) :
          windowPermissions.request(contents, permission, callback, details));
      window.webContents.session.setDisplayMediaRequestHandler(audio.display);
      window.webContents.on("did-start-navigation", windowPermissions.navigation);
      window.webContents.on("did-start-navigation", audio.navigation);
      window.webContents.on("render-process-gone", audio.revoke);
      window.webContents.on("destroyed", audio.revoke);
      window.on("blur", audio.revoke);
      // Full screen hides the traffic lights; the web then gives their inset back to the title bar row.
      window.on("enter-full-screen", () => window.webContents.send("hz:fullscreen", true));
      window.on("leave-full-screen", () => window.webContents.send("hz:fullscreen", false));
      guardUiNavigation(window.webContents, connection);
      window.on("closed", () => { audio.revoke(); windowPermissions.revoke(); if (!quitting) app.quit(); });
    },
  }),
});

const miniBrowser = createMiniBrowser({
  windows,
  fromWebContents: (contents) => BrowserWindow.fromWebContents(contents),
  connection: () => (stopping || quitting ? null : instance.currentConnection()),
  createSession: (partition, options) => session.fromPartition(partition, options),
  createView: (options) => new WebContentsView(options),
  enabled: async () => {
    const connection = instance.currentConnection();
    if (!connection || stopping || quitting) return false;
    const response = await fetch(`${connection.origin}/api/system/config/show`, {
      headers: { Authorization: `Bearer ${connection.token}` }, redirect: "error",
      signal: AbortSignal.timeout(5000),
    });
    if (!response.ok) return false;
    const report = await response.json();
    const features = report.settings?.find((row) => row.section === "hz" && row.key === "features")?.value;
    return Array.isArray(features) && features.includes("sidebar_browser");
  },
});
const managedMac = app.isPackaged && process.platform === "darwin";
const ownedCli = createOwnedCli({ executable: app.isPackaged ? installedCli : () => "hz" });
if (identityReady && instance.acquire(process.argv.slice(app.isPackaged ? 1 : 2))) {
  ipcMain.handle("hz:browser", (event, request) => miniBrowser.handle(event, request));
  ipcMain.handle("hz:public-media", createPublicMediaHandler({
    windows,
    fromWebContents: (contents) => BrowserWindow.fromWebContents(contents),
    connection: () => (stopping || quitting ? null : instance.currentConnection()),
    openExternal: (url) => shell.openExternal(url, { activate: true }),
  }));
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
    miniBrowser.closeAll();
    workspaceAudio?.revoke();
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
    return (managedMac ? acquireDesktopGate() : Promise.resolve(null)).then((gate) => {
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
