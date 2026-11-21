// The IPC handlers of the product windows and of the Connection window.
import path from "node:path";
import { BrowserWindow, app, ipcMain, shell } from "electron";
import { lifecycle } from "./connection.js";
import { createAboutHandler, createAboutLinkHandler, createLifecycleHandler } from "./lifecycle-ipc.js";
import { createPublicMediaHandler, isOwnedMainFrame } from "./public-media.js";
import { isPickerFrame } from "./workspace-controller.js";
import { settings } from "./main-services.js";
import { activations, ctx, pickerURL, windows } from "./main-state.js";

export function registerIpc() {
  ipcMain.handle("hz:browser", (event, request) => ctx.miniBrowser.handle(event, request));
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
    about: () => ({
      version: app.getVersion(),
      // A packaged app runs from <name>.app/Contents/MacOS/<exe>; show the bundle, not the inner file.
      path: app.isPackaged ? path.resolve(app.getPath("exe"), "..", "..", "..") : app.getAppPath(),
    }),
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
    ipcMain.handle("hz:workspace", async (event, request) => {
      if (!isPickerFrame(event, ctx.picker, pickerURL)) throw new Error("Unknown window or frame.");
      if (!request || typeof request !== "object") throw new Error("Unknown workspace request.");
      if (request.kind === "back") { if (ctx.workspaceController.state().current) ctx.picker.close(); }
      else if (request.kind === "forget") ctx.workspaceController.forget(request.origin);
      else if (request.kind === "retry") await ctx.workspaceController.retry();
      else if (["local", "link", "recent"].includes(request.kind)) await ctx.workspaceController.connect(request);
      if (ctx.workspaceController.state().message === "Connected" && ctx.picker && !ctx.picker.isDestroyed()) ctx.picker.hide();
      return { ...ctx.workspaceController.state(), version: app.getVersion() };
    });
}
