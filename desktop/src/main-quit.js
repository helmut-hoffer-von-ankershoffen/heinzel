// The deliberate quit: hide the windows, stop the owned service, refresh after it, or stay open on failure.
import { spawn } from "node:child_process";
import { BrowserWindow, app, dialog } from "electron";
import { installedCli } from "./owned-cli.js";
import { hideWindows, quitLogLine, refreshAfterQuit, shouldRefresh, showWindows, stopWithReport } from "./shutdown.js";
import { ctx } from "./main-state.js";

export function registerQuitHandler() {
  app.on("before-quit", (event) => {
    if (ctx.quitting) return;
    event.preventDefault();
    if (ctx.stopping) return;
    // Every quit that reaches here is deliberate (the menu, the last window, a signal, the installer, or the shell after
    // it already reported a crash). Hide the windows before the service stops so none repaints the disconnected page.
    ctx.stopping = true;
    ctx.permissions?.revoke();
    ctx.browserBridge.stop();
    ctx.mediaPlayer.closeAll();
    ctx.miniBrowser.closeAll();
    ctx.workspaceAudio?.revoke();
    const hidden = hideWindows(BrowserWindow.getAllWindows());
    let unclean = false;
    void stopWithReport(ctx.ownedCli, () => {
      // A service stop that this quit asked for is never reported as a crash; the log keeps what happened.
      unclean = true;
    }).then(async (result) => {
      if (unclean) console.warn(quitLogLine(result));
      const eligible = shouldRefresh({
        packaged: app.isPackaged, platform: process.platform, fixture: Boolean(process.env.HZ_NATIVE_TEST_ROOT),
        serviceStarted: ctx.serviceStarted, unexpectedExit: ctx.unexpectedExitReported, signal: ctx.signalQuit.received(), outcome: result.outcome,
      });
      if (eligible && ctx.desktopGate) {
        ctx.gateReleasing = true;
        await refreshAfterQuit({ eligible, gate: ctx.desktopGate, executable: installedCli, spawn });
      }
      // Cookies imported for this session only end with it.
      await ctx.cookieImport?.dropSessionOnly().catch(() => {});
      ctx.quitting = true;
      if (!ctx.signalQuit.exitIfUnclean(unclean)) app.quit();
    }).catch(() => {
      ctx.stopping = false;
      if (ctx.signalQuit.received()) {
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
}
