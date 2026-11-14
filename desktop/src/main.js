import { spawn } from "node:child_process";
import { release } from "node:os";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { app, BrowserWindow, dialog, ipcMain, Menu, session, shell, WebContentsView, webContents } from "electron";
import { appMenuTemplate, helpLogLine, isAllowedHelpUrl, menuLogLine, menuScript } from "./app-menu.js";
import { lifecycle } from "./connection.js";
import { createInstanceLifecycle } from "./instance-lifecycle.js";
import { createAboutHandler, createAboutLinkHandler, createLifecycleHandler } from "./lifecycle-ipc.js";
import { createMediaPlayer } from "./media-player.js";
import { createMiniBrowser, deviceDialog } from "./mini-browser.js";
import { acquireDesktopGate, createOwnedCli, installedCli } from "./owned-cli.js";
import { createNativePermissions } from "./native-permissions.js";
import { createSettingsStore } from "./settings-store.js";
import { createPublicMediaHandler, isOwnedMainFrame } from "./public-media.js";
import { createWorkspaceAudio, systemAudioAvailable } from "./workspace-audio.js";
import { initializeDesktopIdentity } from "./identity.js";
import { hideWindows, quitLogLine, refreshAfterQuit, shouldRefresh, showWindows, stopWithReport } from "./shutdown.js";
import { installSignalQuit } from "./signal-quit.js";
import { guardUiNavigation, openProductUi } from "./ui-navigation.js";
import { windowOptions } from "./window-options.js";

const identityReady = initializeDesktopIdentity(app, dialog);
const windows = new WeakSet();
const activations = new WeakMap();
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
        system: systemAudioAvailable(process.platform, release()),
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
      activations.set(window.webContents, guardUiNavigation(window.webContents, connection, (url) => shell.openExternal(url), {
        browserOn: () => settings().get("linksInBrowser"),
        browser: async (url) => {
          await miniBrowser.openLink(window, url);
          // Show the panel on the new tab: the page opens it without toggling it shut.
          if (!window.isDestroyed()) void window.webContents.executeJavaScript(menuScript("browser-show")).catch(() => {});
        },
      }));
      window.on("closed", () => { audio.revoke(); windowPermissions.revoke(); if (!quitting) app.quit(); });
    },
  }),
});

// Settings > Browser > "Open links in the built-in browser": a setting of the app itself, live and kept across restarts.
let settingsStore;
const settings = () => (settingsStore ??= createSettingsStore({ file: path.join(app.getPath("userData"), "settings.json") }));
const miniBrowser = createMiniBrowser({
  windows,
  fromWebContents: (contents) => BrowserWindow.fromWebContents(contents),
  connection: () => (stopping || quitting ? null : instance.currentConnection()),
  createSession: (partition, options) => session.fromPartition(partition, options),
  createView: (options) => new WebContentsView(options),
  // The person picks the device or cancels; nothing is granted without that choice.
  chooseDevice: async ({ window, host, devices }) => {
    const { response } = await dialog.showMessageBox(window, deviceDialog({ host, devices }));
    return devices[response]?.id ?? null;
  },
  // "Open in browser" needs the person's click in that window and shares its external-link rate limit.
  openExternal: (url, window) => activations.get(window.webContents)?.open(url) ?? false,
});
// The media player's embed shares the sidebar browser's website partition, so one sign-in counts in both.
const mediaPlayer = createMediaPlayer({
  windows,
  fromWebContents: (contents) => BrowserWindow.fromWebContents(contents),
  connection: () => (stopping || quitting ? null : instance.currentConnection()),
  lease: () => miniBrowser.lease(),
  createView: (options) => new WebContentsView(options),
  openExternal: (url) => shell.openExternal(url),
});
const managedMac = app.isPackaged && process.platform === "darwin";
const ownedCli = createOwnedCli({ executable: app.isPackaged ? installedCli : () => "hz" });
if (identityReady && instance.acquire(process.argv.slice(app.isPackaged ? 1 : 2))) {
  ipcMain.handle("hz:browser", (event, request) => miniBrowser.handle(event, request));
  ipcMain.handle("hz:player", (event, request) => mediaPlayer.handle(event, request));
  ipcMain.handle("hz:link-mode", (event, inBrowser) => {
    const owned = isOwnedMainFrame(event, {
      windows,
      fromWebContents: (contents) => BrowserWindow.fromWebContents(contents),
      connection: () => (stopping || quitting ? null : instance.currentConnection()),
    });
    if (owned && typeof inBrowser === "boolean") settings().set("linksInBrowser", inBrowser);
    return settings().get("linksInBrowser");
  });
  ipcMain.handle("hz:public-media", createPublicMediaHandler({
    windows,
    fromWebContents: (contents) => BrowserWindow.fromWebContents(contents),
    connection: () => (stopping || quitting ? null : instance.currentConnection()),
    openExternal: (url) => shell.openExternal(url, { activate: true }),
  }));
  // Trusted input reported by the isolated preload is the only gesture that lets one external link open.
  ipcMain.handle("hz:user-activation", (event) => {
    try {
      if (isOwnedMainFrame(event, {
        windows,
        fromWebContents: (contents) => BrowserWindow.fromWebContents(contents),
        connection: () => (stopping || quitting ? null : instance.currentConnection()),
      })) activations.get(event.sender)?.grant();
    } catch {
      // A destroyed sender grants nothing.
    }
  });
  ipcMain.handle("hz:lifecycle", createLifecycleHandler({
    windows,
    fromWebContents: (contents) => BrowserWindow.fromWebContents(contents),
    connection: () => instance.currentConnection(),
    lifecycle,
  }));
  ipcMain.handle("hz:about", createAboutHandler({
    windows,
    fromWebContents: (contents) => BrowserWindow.fromWebContents(contents),
    connection: () => instance.currentConnection(),
    about: () => ({
      version: app.getVersion(),
      // A packaged app runs from <name>.app/Contents/MacOS/<exe>; show the bundle, not the inner file.
      path: app.isPackaged ? path.resolve(app.getPath("exe"), "..", "..", "..") : app.getAppPath(),
    }),
  }));
  ipcMain.handle("hz:about-link", createAboutLinkHandler({
    windows,
    fromWebContents: (contents) => BrowserWindow.fromWebContents(contents),
    connection: () => instance.currentConnection(),
    open: (url) => shell.openExternal(url),
    log: (entry) => console.info(JSON.stringify(entry)),
  }));
  // The visualizer's desktop sources: whether system loopback exists here, and the next capture's source.
  ipcMain.handle("hz:system-audio", () => workspaceAudio?.available() === true);
  ipcMain.handle("hz:audio-source", (event, source) => workspaceAudio?.choose(event, source) === true);
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
    // Every quit that reaches here is deliberate (the menu, the last window, a signal, the installer, or the shell after
    // it already reported a crash). Hide the windows before the service stops so none repaints the disconnected page.
    stopping = true;
    permissions?.revoke();
    mediaPlayer.closeAll();
    miniBrowser.closeAll();
    workspaceAudio?.revoke();
    const hidden = hideWindows(BrowserWindow.getAllWindows());
    let unclean = false;
    void stopWithReport(ownedCli, () => {
      // A service stop that this quit asked for is never reported as a crash; the log keeps what happened.
      unclean = true;
    }).then(async (result) => {
      if (unclean) console.warn(quitLogLine(result));
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
      showWindows(hidden);
      dialog.showErrorBox(
        "Heinzelmännchen is still running",
        "The local service did not stop. The app will stay open so you can try Quit again.",
      );
    });
  });
  void app.whenReady().then(() => {
    if (stopping) return;
    // The native About panel is never the target (the menu opens the web dialog), but if macOS shows it anyway it
    // names the app properly and shows one version, not "Version X (X)".
    app.setAboutPanelOptions({ applicationName: app.name, applicationVersion: app.getVersion(), version: "" });
    // The page owns what About, Settings and Updates show; the menu only asks it to open them.
    if (process.platform === "darwin") Menu.setApplicationMenu(Menu.buildFromTemplate(appMenuTemplate({
      name: app.name,
      devTools: !app.isPackaged,
      open: (url) => {
        const allowed = isAllowedHelpUrl(url);
        console.info(helpLogLine(url, allowed));
        if (allowed) void shell.openExternal(url);
      },
      send: (action) => {
        // Close Tab acts on the focused window only: with none focused it must never close a background window.
        const window = action === "close-tab" ? BrowserWindow.getFocusedWindow()
          : BrowserWindow.getFocusedWindow() ?? BrowserWindow.getAllWindows()[0];
        const ready = Boolean(window && windows.has(window) && !window.isDestroyed());
        console.info(menuLogLine(action, ready ? "sent" : "no_window"));
        if (!ready) return;
        const sent = window.webContents.executeJavaScript(menuScript(action));
        // Cmd+W closes the browser's selected tab when the page takes it, and otherwise the window as before.
        if (action === "close-tab") void sent.then((taken) => {
          console.info(menuLogLine(action, taken === true ? "taken" : "not_taken"));
          if (taken !== true && !window.isDestroyed()) window.close();
        }, () => {});
        else void sent;
      },
    })));
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
