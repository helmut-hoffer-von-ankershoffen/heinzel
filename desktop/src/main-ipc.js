import { createAlbumTransport } from "./album-transport.js";
// The IPC handlers of the product windows and of the Connection window.
import { app, BrowserWindow, clipboard, ipcMain, shell } from "electron";
import { lifecycle } from "./connection.js";
import { createAboutHandler, createAboutLinkHandler, createLifecycleHandler } from "./lifecycle-ipc.js";
import { createPublicMediaHandler, isOwnedMainFrame } from "./public-media.js";
import { isPickerFrame } from "./workspace-controller.js";
import { applicationInfo, settings } from "./main-services.js";
import { showConnection } from "./main-picker.js";
import { workspaceName } from "./workspace-directory.js";
import { activations, ctx, pickerURL, productWindows, windows } from "./main-state.js";

export function registerIpc() {
  const ownsAlbum = (event) => isOwnedMainFrame(event, {
    windows: productWindows, fromWebContents: contents => BrowserWindow.fromWebContents(contents),
    connection: () => ctx.stopping || ctx.quitting ? null : ctx.instance.currentConnection(),
  }) && ctx.instance.currentWindow()?.webContents === event.sender;
  const albumTransport = createAlbumTransport({ BrowserWindow, app, publish: snapshot => {
    const contents = ctx.instance.currentWindow()?.webContents;
    if (contents && ownsAlbum({ sender: contents, senderFrame: contents.mainFrame }))
      contents.send("hz:album-change", snapshot);
  } });
  ipcMain.handle("hz:album", async (event, action, options) => {
    if (!ownsAlbum(event)) throw new Error("Unknown album frame.");
    if (!["status", "catalog"].includes(action) && !activations.get(event.sender)?.consume())
      throw new Error("Use an album control.");
    const frame = event.senderFrame;
    const result = await albumTransport.handle(action, options, () => ownsAlbum(event) && event.sender.mainFrame === frame);
    if (!ownsAlbum(event) || event.sender.mainFrame !== frame) throw new Error("Album document changed.");
    return result;
  });
  ipcMain.handle("hz:browser", (event, request) => ctx.miniBrowser.handle(event, request));
  ipcMain.handle("hz:player-chrome", (event, request) => ctx.mediaPlayer.chromeHandle(event, request));
  ipcMain.handle("hz:player", (event, request) => ctx.mediaPlayer.handle(event, request));
  ipcMain.handle("hz:link-mode", (event, inBrowser) => {
    const owned = isOwnedMainFrame(event, {
      windows,
      fromWebContents: (contents) => BrowserWindow.fromWebContents(contents),
      connection: () => (ctx.stopping || ctx.quitting ? null : ctx.instance.currentConnection()),
    });
    if (owned && typeof inBrowser === "boolean") settings().set("linksInBrowser", inBrowser);
    return settings().get("linksInBrowser");
  });
  ipcMain.handle("hz:public-media", createPublicMediaHandler({
    windows,
    fromWebContents: (contents) => BrowserWindow.fromWebContents(contents),
    connection: () => (ctx.stopping || ctx.quitting ? null : ctx.instance.currentConnection()),
    openExternal: (url) => shell.openExternal(url, { activate: true }),
  }));
  // Trusted input reported by the isolated preload is the only gesture that lets one external link open.
  ipcMain.handle("hz:user-activation", (event) => {
    try {
      if (isOwnedMainFrame(event, {
        windows,
        fromWebContents: (contents) => BrowserWindow.fromWebContents(contents),
        connection: () => (ctx.stopping || ctx.quitting ? null : ctx.instance.currentConnection()),
      })) activations.get(event.sender)?.grant();
    } catch {
      // A destroyed sender grants nothing.
    }
  });
  ipcMain.handle("hz:lifecycle", createLifecycleHandler({
    windows,
    fromWebContents: (contents) => BrowserWindow.fromWebContents(contents),
    connection: () => ctx.instance.currentConnection(),
    lifecycle,
  }));
  ipcMain.handle("hz:about", createAboutHandler({
    windows,
    fromWebContents: (contents) => BrowserWindow.fromWebContents(contents),
    connection: () => ctx.instance.currentConnection(),
    about: applicationInfo,
  }));
  ipcMain.handle("hz:about-link", createAboutLinkHandler({
    windows,
    fromWebContents: (contents) => BrowserWindow.fromWebContents(contents),
    connection: () => ctx.instance.currentConnection(),
    open: (url) => shell.openExternal(url),
    log: (entry) => console.info(JSON.stringify(entry)),
  }));
  // The visualizer's desktop sources: whether system loopback exists here, and the next capture's source.
  ipcMain.handle("hz:system-audio", () => ctx.workspaceAudio?.available() === true);
  ipcMain.handle("hz:audio-source", (event, source) => ctx.workspaceAudio?.choose(event, source) === true);
}

export function registerWorkspaceIpc() {
  ipcMain.handle("hz:workspace-menu", (event, action) => {
    const window = ctx.instance.currentWindow();
    if (!window || window.webContents !== event.sender || event.senderFrame !== event.sender.mainFrame ||
        !isOwnedMainFrame(event, { windows: productWindows, fromWebContents: (contents) => BrowserWindow.fromWebContents(contents),
          connection: () => ctx.instance.currentConnection() })) throw new Error("Unknown window or frame.");
    const target = ctx.instance.currentConnection();
    if (action === "name") return target.remote ? workspaceName(target.origin) : "This Mac";
    if (action === "open") { showConnection(); return true; }
    return false;
  });
  ipcMain.handle("hz:workspace", async (event, request) => {
    if (!isPickerFrame(event, ctx.picker, pickerURL)) throw new Error("Unknown window or frame.");
    if (!request || typeof request !== "object") throw new Error("Unknown workspace request.");
    const controller = ctx.workspaceController, picker = ctx.picker;
    const active = controller.state().current;
    switch (request.kind) {
      case "notifications": {
        const contents = ctx.instance.currentWindow()?.webContents;
        if (!contents || !ctx.permissions || ctx.monitor.state()) break;
        ctx.monitor.hold(true);
        try {
          await new Promise((done) => ctx.permissions.request(contents, "notifications", done,
            { isMainFrame: true, requestingUrl: contents.mainFrame.url }));
        } finally { ctx.monitor.hold(ctx.editing === true); }
        break;
      }
      case "paste": return clipboard.readText().slice(0, 4097);
      case "pause": ctx.monitor.pause(request.origin); break;
      case "resume": ctx.monitor.resume(request.origin); break;
      case "network": ctx.monitor.wake(); ctx.refreshPicker(); break;
      case "information": ctx.showInformation(request.show === true); break;
      case "edit": ctx.editing = true; controller.cancel(); break;
      case "cancel": ctx.editing = false; controller.cancel(); break;
      case "back":
        controller.cancel();
        if (active && !ctx.monitor.state()) picker.close();
        break;
      case "disconnect":
        if (active && active === request.origin) {
          ctx.selected(null); controller.cancel();
          showConnection(true);
          ctx.browserBridge.stop(); ctx.instance.disconnect();
        }
        break;
      case "forget":
        if (request.origin !== active) { controller.cancel(); controller.forget(request.origin); }
        break;
      case "local": case "link": case "recent": case "retry": {
        const revision = ctx.selectionRevision = (ctx.selectionRevision ?? 0) + 1;
        const state = request.kind === "retry" ? await controller.retry()
          : await controller.connect(request, request.force === true || Boolean(ctx.monitor.state() && request.kind === "local"));
        if (revision === ctx.selectionRevision && !picker.isDestroyed() && !state.busy && !state.problem &&
            state.message.startsWith("Connected") && !ctx.monitor.state()) picker.hide();
        break;
      }
    }
    return ctx.pickerState();
  });
}
