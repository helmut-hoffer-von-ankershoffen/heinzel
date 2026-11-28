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

export function showConnection(full = false) {
  if (ctx.stopping || ctx.quitting) return;
  full ||= Boolean(ctx.monitor?.state());
  const source = ctx.instance.currentWindow();
  const parent = full ? null : source;
  const previous = ctx.picker;
  if (previous && !previous.isDestroyed() && ctx.pickerParent === parent) {
    ctx.picker.show(); ctx.picker.focus();
    ctx.refreshPicker?.();
    return;
  }
  ctx.pickerLoading = true;
  ctx.pickerParent = parent;
  ctx.picker = new BrowserWindow({ width: 510, height: 690, minWidth: 320, minHeight: 420, title: "Workspaces · Heinzelmännchen",
    ...(parent ? { parent, modal: true } : source?.getBounds?.()),
    backgroundColor: "#faf8f3", show: false,
    webPreferences: { preload: fileURLToPath(new URL("./workspace-picker-preload.cjs", import.meta.url)),
      contextIsolation: true, nodeIntegration: false, sandbox: true, partition: `picker-${randomUUID()}` },
  });
  ctx.picker.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  ctx.picker.webContents.on("will-navigate", (event) => event.preventDefault());
  ctx.picker.webContents.session.setPermissionCheckHandler(() => false);
  ctx.picker.webContents.session.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  const picker = ctx.picker;
  picker.on("closed", () => {
    if (ctx.picker !== picker) return;
    ctx.pickerLoading = false; ctx.picker = null; ctx.editing = false;
    // macOS closes a sheet with its parent after a verified successor becomes the current window.
    const current = ctx.instance.currentWindow();
    if (!parent || !current || current === parent) ctx.workspaceController?.cancel();
    if (!BrowserWindow.getAllWindows().length && !ctx.switching) app.quit();
  });
  picker.on("close", (event) => {
    if (full && ctx.monitor.state() && !ctx.quitting && !ctx.stopping) event.preventDefault();
  });
  if (previous && !previous.isDestroyed()) previous.destroy();
  void picker.loadURL(pickerURL).then(() => {
    if (ctx.picker !== picker || picker.isDestroyed()) return;
    ctx.pickerLoading = false;
    if (full && source && !source.isDestroyed() && ctx.monitor.state()) source.hide();
    if (!full || !source || ctx.monitor.state() || !ctx.instance.currentWindow()) picker.show();
    ctx.refreshPicker?.();
  }).catch(() => { if (!picker.isDestroyed()) picker.close(); });
}
