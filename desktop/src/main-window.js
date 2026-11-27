// The product window: its session, permissions, audio, link guards and what happens when it closes.
import { randomUUID } from "node:crypto";
import { release } from "node:os";
import { fileURLToPath } from "node:url";
import { BrowserWindow, app, dialog, session, shell, webContents } from "electron";
import { installLegacyWorkspaceMenu, menuScript } from "./app-menu.js";
import { lifecycle } from "./connection.js";
import { attachClientHeader, machineName } from "./client-identity.js";
import { createInstanceLifecycle } from "./instance-lifecycle.js";
import { createNativePermissions } from "./native-permissions.js";
import { guardUiNavigation, openProductUi } from "./ui-navigation.js";
import { verifyWorkspaceRenderer } from "./workspace-attachment.js";
import { windowOptions } from "./window-options.js";
import { createWorkspaceAudio, systemAudioAvailable } from "./workspace-audio.js";
import { showConnection } from "./main-picker.js";
import { settings } from "./main-services.js";
import { activations, ctx, productWindows, windows } from "./main-state.js";

export function createWindowLifecycle() {
  return createInstanceLifecycle({
    app,
    parseConnection: () => null,
    focusWindow: (window) => {
      if (!window.isVisible()) window.show();
      window.focus();
    },
    createWindow: (connection, superseded) => openProductUi({
      connection,
      lifecycle: async (target) => {
        const state = await lifecycle(target);
        if (!target.remote) ctx.localBackendVersion = state.version;
        return state;
      },
      cancelled: () => ctx.stopping || ctx.quitting || superseded?.(),
      verifyRenderer: verifyWorkspaceRenderer,
      createWindow: async () => {
        const workspaceSession = session.fromPartition(`workspace-${randomUUID()}`);
        if (!connection.remote) await workspaceSession.setProxy({ mode: "direct" });
        attachClientHeader(workspaceSession, connection.origin, () => ({
          appVersion: app.getVersion(), backendVersion: ctx.localBackendVersion, machine: machineName(),
        }));
        return new BrowserWindow({
        ...windowOptions(process.platform),
        webPreferences: {
          preload: fileURLToPath(new URL(connection.remote ? "./workspace-remote-preload.cjs" : "./preload.cjs", import.meta.url)),
          session: workspaceSession,
          contextIsolation: true,
          nodeIntegration: false,
          sandbox: true,
          webviewTag: false,
        },
        });
      },
      prepareWindow: (window, server) => {
        if (server?.version) ctx.windowVersions.set(window, server.version);
        productWindows.add(window);
        window.webContents.on("dom-ready", () => {
          void window.webContents.executeJavaScript(`(${installLegacyWorkspaceMenu})(${JSON.stringify(connection.origin)})`).catch(() => {});
        });
        const workspaceSession = window.webContents.session;
        window.on("closed", () => {
          void workspaceSession.clearStorageData().catch(() => {});
          void workspaceSession.clearCache().catch(() => {});
          if (!ctx.switching && !ctx.startingProductWindow && !ctx.pickerLoading && !ctx.quitting && !ctx.picker?.isVisible()) app.quit();
        });
        const recover = () => {
          if (ctx.switching || ctx.stopping || ctx.quitting || window !== ctx.instance.currentWindow()) return;
          ctx.rendererFailed = true; ctx.monitor?.select(null);
          ctx.workspaceController?.failed(connection, new Error("Workspace unavailable"));
          showConnection();
        };
        window.webContents.on("did-fail-load", (_event, code, _description, _url, mainFrame) => {
          if (mainFrame && code !== -3) recover();
        });
        window.webContents.on("render-process-gone", recover);
        if (connection.remote) {
          workspaceSession.setPermissionCheckHandler(() => false);
          workspaceSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
          guardUiNavigation(window.webContents, connection, (url) => shell.openExternal(url));
          return;
        }
        if (!superseded) windows.add(window);
        const windowPermissions = createNativePermissions({
          windows,
          fromWebContents: (contents) => BrowserWindow.fromWebContents(contents),
          connection: () => (ctx.stopping || ctx.quitting ? null : ctx.instance.currentConnection()),
          prompt: (owner, options) => dialog.showMessageBox(owner, options),
        });

        const audio = createWorkspaceAudio({
          windows,
          fromWebContents: (contents) => BrowserWindow.fromWebContents(contents),
          fromFrame: (frame) => frame ? webContents.fromFrame(frame) : null,
          connection: () => (ctx.stopping || ctx.quitting ? null : ctx.instance.currentConnection()),
          prompt: (owner, options) => dialog.showMessageBox(owner, options),
          system: systemAudioAvailable(process.platform, release()),
        });
        ctx.windowCapabilities.set(window, { permissions: windowPermissions, audio });
        if (!superseded) { ctx.workspaceAudio = audio; ctx.permissions = windowPermissions; }
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
            await ctx.miniBrowser.openLink(window, url);
            // Show the panel on the new tab: the page opens it without toggling it shut.
            if (!window.isDestroyed()) void window.webContents.executeJavaScript(menuScript("browser-show")).catch(() => {});
          },
        }));
        window.on("closed", () => { audio.revoke(); windowPermissions.revoke(); });
      },
    }),
  });
}
