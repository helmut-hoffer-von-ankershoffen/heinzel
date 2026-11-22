// The "Import cookies from your browser" window: opened only from the app menu, never by a page.
import { randomUUID } from "node:crypto";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { BrowserWindow, app, ipcMain, session } from "electron";
import { createCookieImport, isDialogFrame } from "./cookie-import.js";
import { BROWSER_PARTITION } from "./mini-browser.js";
import { ctx } from "./main-state.js";

const cookieDialogURL = new URL("./cookie-import.html", import.meta.url).href;
let cookieDialog = null;

/** Once the app is ready. The importer writes into the browser's website partition only: never the product's session, never the service. */
export function registerCookieImport() {
  if (process.platform !== "darwin") return;
  ctx.cookieImport = createCookieImport({
    session: () => session.fromPartition(BROWSER_PARTITION),
    ledgerFile: path.join(app.getPath("userData"), "imported-cookies.json"),
  });
  ipcMain.handle("hz:cookie-import", async (event, request) => {
    if (!isDialogFrame(event, cookieDialog, cookieDialogURL)) throw new Error("Unknown window or frame.");
    return ctx.cookieImport.handle(request);
  });
  ipcMain.handle("hz:cookie-import-close", async (event) => {
    if (isDialogFrame(event, cookieDialog, cookieDialogURL)) cookieDialog.close();
  });
  // Session-only cookies of a run that ended without a clean quit go now.
  void ctx.cookieImport.dropSessionOnly().catch(() => {});
}

/** Shows the dialog on its first view ("import") or on "Manage imported cookies" ("manage"). */
export function showCookieImport(view = "import") {
  if (!ctx.cookieImport || ctx.stopping || ctx.quitting) return;
  if (cookieDialog && !cookieDialog.isDestroyed()) {
    cookieDialog.webContents.send("hz:cookie-import-view", view);
    cookieDialog.show(); cookieDialog.focus();
    return;
  }
  cookieDialog = new BrowserWindow({ width: 520, height: view === "manage" ? 520 : 600, minWidth: 420, minHeight: 360,
    title: "Import Cookies · Heinzelmännchen", backgroundColor: "#faf8f3", show: false,
    parent: BrowserWindow.getFocusedWindow() ?? undefined,
    webPreferences: { preload: fileURLToPath(new URL("./cookie-import-preload.cjs", import.meta.url)),
      contextIsolation: true, nodeIntegration: false, sandbox: true, partition: `cookie-import-${randomUUID()}` },
  });
  const contents = cookieDialog.webContents;
  contents.setWindowOpenHandler(() => ({ action: "deny" }));
  contents.on("will-navigate", (event) => event.preventDefault());
  contents.session.setPermissionCheckHandler(() => false);
  contents.session.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  cookieDialog.on("closed", () => { cookieDialog = null; });
  void cookieDialog.loadURL(cookieDialogURL).then(() => {
    if (!cookieDialog || cookieDialog.isDestroyed()) return;
    if (view === "manage") contents.send("hz:cookie-import-view", "manage");
    cookieDialog.show();
  });
}
