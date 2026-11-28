// The in-process services: settings, the mini browser, the media player, the agent bridge and the owned service.
import path from "node:path";
import { BrowserWindow, WebContentsView, app, dialog, nativeImage, session, shell } from "electron";
import { createBrowserAgent } from "./browser-agent.js";
import { createBrowserBridge, newBridgeToken } from "./browser-bridge.js";
import { lifecycle } from "./connection.js";
import { createMediaPlayer } from "./media-player.js";
import { createMiniBrowser, deviceDialog, smallFavicon } from "./mini-browser.js";
import { createOwnedCli, developmentCli, installedCli } from "./owned-cli.js";
import { createSettingsStore } from "./settings-store.js";
import { activations, ctx, windows } from "./main-state.js";

// Settings > Browser > "Open links in the built-in browser": a setting of the app itself, live and kept across restarts.
export const settings = () => (ctx.settingsStore ??= createSettingsStore({ file: path.join(app.getPath("userData"), "settings.json") }));

/** Runtime metadata for the packaged picker and the owned local About dialog. */
export function applicationInfo() {
  const platform = { darwin: "macOS", win32: "Windows", linux: "Linux" }[process.platform] ?? process.platform;
  const executable = app.getPath("exe");
  return {
    type: "Electron",
    version: app.getVersion(),
    path: !app.isPackaged ? app.getAppPath()
      : process.platform === "darwin" ? path.resolve(executable, "..", "..", "..") : executable,
    operatingSystem: [platform, process.getSystemVersion()].filter(Boolean).join(" "),
  };
}

export function createServices() {
  ctx.miniBrowser = createMiniBrowser({
    windows,
    fromWebContents: (contents) => BrowserWindow.fromWebContents(contents),
    connection: () => (ctx.stopping || ctx.quitting ? null : ctx.instance.currentConnection()),
    createSession: (partition, options) => session.fromPartition(partition, options),
    createView: (options) => new WebContentsView(options),
    // The person picks the device or cancels; nothing is granted without that choice.
    chooseDevice: async ({ window, host, devices }) => {
      const { response } = await dialog.showMessageBox(window, deviceDialog({ host, devices }));
      return devices[response]?.id ?? null;
    },
    // "Open in browser" needs the person's click in that window and shares its external-link rate limit.
    openExternal: (url, window) => activations.get(window.webContents)?.open(url) ?? false,
    // A page's icon becomes a 32-pixel PNG: small enough for a tab, a bookmark and the navigation.
    faviconImage: (buffer) => smallFavicon(buffer, (bytes) => nativeImage.createFromBuffer(bytes)),
  });
  // The media player's embed shares the sidebar browser's website partition, so one sign-in counts in both.
  ctx.mediaPlayer = createMediaPlayer({
    windows,
    fromWebContents: (contents) => BrowserWindow.fromWebContents(contents),
    connection: () => (ctx.stopping || ctx.quitting ? null : ctx.instance.currentConnection()),
    // The player hands its page, so Clear browsing data blanks and restores the YouTube player too.
    lease: (page) => ctx.miniBrowser.lease(page),
    createView: (options) => new WebContentsView(options),
    openExternal: (url) => shell.openExternal(url),
  });
  // Agents drive their own tabs through the daemon (ADR 0162): one socket to the local service, and an engine that only
  // ever gets guests of the browser's tab table.
  const ownedWindows = () => BrowserWindow.getAllWindows().filter((window) => windows.has(window) && !window.isDestroyed());
  // Known to this process and to the service it starts, and to no window: the product page holds the session token only.
  ctx.bridgeToken = newBridgeToken();
  ctx.browserBridge = createBrowserBridge({
    connection: () => (ctx.stopping || ctx.quitting ? null : ctx.instance.currentConnection()),
    token: ctx.bridgeToken,
    engine: createBrowserAgent({
      tabs: ctx.miniBrowser.agents,
      window: () => {
        const focused = BrowserWindow.getFocusedWindow();
        return focused && windows.has(focused) && !focused.isDestroyed() ? focused : ownedWindows()[0] ?? null;
      },
    }),
    listen: ctx.miniBrowser.agents.listen,
  });
  ctx.managedMac = app.isPackaged && process.platform === "darwin";
  ctx.ownedCli = createOwnedCli({ executable: app.isPackaged ? installedCli : () => developmentCli(), bridgeToken: ctx.bridgeToken, probe: (connection) => ctx.probeLocal ? ctx.probeLocal(connection) : lifecycle(connection), probeContext: () => ctx.foreground === false ? null : ctx.probeEpoch ?? 0, env: { HZ_CALLER: "desktop-app" } });
}
