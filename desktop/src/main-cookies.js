// The "Import cookies from your browser" window: opened only from the app menu, never by a page.
import { randomUUID } from "node:crypto";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { BrowserWindow, app, ipcMain, session } from "electron";
import { createCookieImport, isDialogFrame } from "./cookie-import.js";
import { BROWSER_PARTITION } from "./mini-browser.js";
import { ctx } from "./main-state.js";

const dialogURL = new URL("./cookie-import.html", import.meta.url).href;
let dialog = null;

/** The importer writes into the browser's website partition only: never the product's session, never the service. */
export function registerCookieImport() {
  if (process.platform !== "darwin") return;
  ctx.cookieImport = createCookieImport({
    session: () => session.fromPartition(BROWSER_PARTITION),
    ledgerFile: path.join(app.getPath("userData"), "imported-cookies.json"),
  });
  ipcMain.handle("hz:cookie-import", (event, request) => {
    if (!isDialogFrame(event, dialog, dialogURL)) throw new Error("Unknown window or frame.");
    return ctx.cookieImport.handle(request);
  });
  ipcMain.handle("hz:cookie-import-close", (event) => {
    if (isDialogFrame(event, dialog, dialogURL)) dialog.close();
  });
  // Session-only cookies of a run that ended without a clean quit go now.
  void app.whenReady().then(() => ctx.cookieImport.dropSessionOnly()).catch(() => {});
}

/** Shows the dialog on its first view ("import") or on "Manage imported cookies" ("manage"). */
export function showCookieImport(view = "import") {
  if (!ctx.cookieImport || ctx.stopping || ctx.quitting) return;
  if (dialog && !dialog.isDestroyed()) {
    dialog.webContents.send("hz:cookie-import-view", view);
    dialog.show(); dialog.focus();
    return;
  }
  dialog = new BrowserWindow({ width: 520, height: view === "manage" ? 520 : 600, minWidth: 420, minHeight: 360,
    title: "Import Cookies · Heinzelmännchen", backgroundColor: "#faf8f3", show: false,
    parent: BrowserWindow.getFocusedWindow() ?? undefined,
    webPreferences: { preload: fileURLToPath(new URL("./cookie-import-preload.cjs", import.meta.url)),
      contextIsolation: true, nodeIntegration: false, sandbox: true, partition: `cookie-import-${randomUUID()}` },
  });
  const contents = dialog.webContents;
  contents.setWindowOpenHandler(() => ({ action: "deny" }));
  contents.on("will-navigate", (event) => event.preventDefault());
  contents.session.setPermissionCheckHandler(() => false);
  contents.session.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  dialog.on("closed", () => { dialog = null; });
  void dialog.loadURL(dialogURL).then(() => {
    if (!dialog || dialog.isDestroyed()) return;
    if (view === "manage") contents.send("hz:cookie-import-view", "manage");
    dialog.show();
  });
}
