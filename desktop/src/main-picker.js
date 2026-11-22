// The Connection window (the workspace picker) and the single-instance desktop gate.
import { randomUUID } from "node:crypto";
import { fileURLToPath } from "node:url";
import { BrowserWindow, app } from "electron";
import { ctx, pickerURL } from "./main-state.js";

export function keepDesktopGate(gate) {
  ctx.desktopGate = gate;
  gate.once("close", () => { if (!ctx.quitting && !ctx.gateReleasing) app.quit(); });
  gate.once("error", () => { if (!ctx.quitting && !ctx.gateReleasing) app.quit(); });
}

export function showConnection() {
  if (ctx.stopping || ctx.quitting) return;
  if (ctx.picker && !ctx.picker.isDestroyed()) {
    ctx.picker.show(); ctx.picker.focus();
    try { void ctx.workspaceController?.inspect(ctx.instance.currentConnection()); } catch { /* No workspace yet. */ }
    return;
  }
  ctx.pickerLoading = true;
  ctx.picker = new BrowserWindow({ width: 510, height: 780, minWidth: 360, title: "Connection · Heinzelmännchen",
    backgroundColor: "#faf8f3", show: false,
    webPreferences: { preload: fileURLToPath(new URL("./workspace-picker-preload.cjs", import.meta.url)),
      contextIsolation: true, nodeIntegration: false, sandbox: true, partition: `picker-${randomUUID()}` },
  });
  ctx.picker.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  ctx.picker.webContents.on("will-navigate", (event) => event.preventDefault());
  ctx.picker.webContents.session.setPermissionCheckHandler(() => false);
  ctx.picker.webContents.session.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  ctx.picker.on("closed", () => { ctx.pickerLoading = false; ctx.picker = null; if (!BrowserWindow.getAllWindows().length && !ctx.switching) app.quit(); });
  void ctx.picker.loadURL(pickerURL).then(() => {
    ctx.pickerLoading = false;
    if (ctx.picker && !ctx.picker.isDestroyed()) ctx.picker.show();
    try { void ctx.workspaceController?.inspect(ctx.instance.currentConnection()); } catch { /* No workspace yet. */ }
  });
}
