// Startup: acquire the gate, start the owned service, open the first product window; or show the Connection window.
import { app } from "electron";
import { acquireDesktopGate } from "./owned-cli.js";
import { createController } from "./main-controller.js";
import { registerWorkspaceIpc } from "./main-ipc.js";
import { installAppMenu } from "./main-menu.js";
import { keepDesktopGate, showConnection } from "./main-picker.js";
import { ctx } from "./main-state.js";

export function startWhenReady() {
  void app.whenReady().then(() => {
    if (ctx.stopping) return;
    createController();
    registerWorkspaceIpc();
    installAppMenu();
    return (ctx.managedMac ? acquireDesktopGate() : Promise.resolve(null)).then((gate) => {
      if (gate) {
        keepDesktopGate(gate);
      }
      if (ctx.stopping) return null;
      return ctx.ownedCli.start();
    }).then((connection) => {
      if (ctx.stopping || !connection) return;
      if (!ctx.ownedCli.isRunning()) throw new Error("The local service stopped during startup.");
      ctx.localConnection = connection;
      ctx.serviceStarted = true;
      // A workspace selected while the local service was starting takes precedence.
      try { ctx.instance.currentConnection(); return; } catch { /* No workspace selected yet. */ }
      ctx.instance.setConnection(connection);
      ctx.instance.setReady();
      ctx.browserBridge.start();
      ctx.startingProductWindow = true;
      return ctx.instance.open().finally(() => { ctx.startingProductWindow = false; });
    });
  }).catch((error) => {
    if (ctx.stopping) return;
    try { if (ctx.instance.currentConnection().remote) return; } catch { /* No selected workspace. */ }
    const message = error instanceof Error && error.message.includes("too long")
      ? "The local service did not become ready within 30 seconds. Check that the installed hz command works, then try again."
      : "The local service could not start. Check that Heinzelmännchen is installed and try again.";
    let failedConnection = null;
    try { failedConnection = ctx.instance.currentConnection(); } catch { /* Service did not start. */ }
    ctx.workspaceController?.failed(failedConnection, new Error(message));
    showConnection();
  });
}
