// The workspace controller: how the app attaches to the local service or a remote workspace.
import path from "node:path";
import { BrowserWindow, app, safeStorage } from "electron";
import { lifecycle } from "./connection.js";
import { acquireDesktopGate, createOwnedCli, developmentCli, installedCli } from "./owned-cli.js";
import { stopWithReport } from "./shutdown.js";
import { createWorkspaceController } from "./workspace-controller.js";
import { createWorkspaceDirectory } from "./workspace-directory.js";
import { keepDesktopGate } from "./main-picker.js";
import { watchLocalService } from "./main-service-watch.js";
import { ctx, productWindows } from "./main-state.js";

export function createController() {
    const directory = createWorkspaceDirectory({ file: path.join(app.getPath("userData"), "workspaces.json"), safeStorage });
    const publishState = (state) => {
      if (ctx.picker && !ctx.picker.isDestroyed()) ctx.picker.webContents.send("hz:workspace-state", { ...state, version: app.getVersion() });
    };
    ctx.workspaceController = createWorkspaceController({
      directory,
      current: () => {
        try { return BrowserWindow.getAllWindows().some((window) => productWindows.has(window)) ? ctx.instance.currentConnection().origin : null; }
        catch { return null; }
      },
      changed: publishState,
      verify: lifecycle,
      local: async () => {
        if (ctx.stopping || ctx.quitting) throw new Error("Desktop is stopping.");
        if (ctx.localConnection && ctx.ownedCli.isRunning()) return ctx.localConnection;
        await stopWithReport(ctx.ownedCli, () => {});
        if (ctx.stopping || ctx.quitting) throw new Error("Desktop is stopping.");
        ctx.ownedCli = createOwnedCli({ executable: app.isPackaged ? installedCli : () => developmentCli(), bridgeToken: ctx.bridgeToken, probe: lifecycle, env: { HZ_CALLER: "desktop-app" } });
        watchLocalService();
        if (ctx.managedMac && !ctx.desktopGate) keepDesktopGate(await acquireDesktopGate());
        if (ctx.stopping || ctx.quitting) throw new Error("Desktop is stopping.");
        ctx.localConnection = await ctx.ownedCli.start();
        ctx.serviceStarted = true;
        return ctx.localConnection;
      },
      replace: async (connection) => {
        if (ctx.stopping || ctx.quitting) throw new Error("Desktop is stopping.");
        ctx.switching = true;
        ctx.browserBridge.stop();
        try {
          ctx.instance.setReady();
          await ctx.instance.replaceConnection(connection);
          if (!connection.remote) ctx.browserBridge.start();
        } catch (error) {
          try { if (!ctx.instance.currentConnection().remote && ctx.ownedCli.isRunning()) ctx.browserBridge.start(); } catch { /* No previous workspace. */ }
          throw error;
        } finally { ctx.switching = false; }
      },
    });
}
