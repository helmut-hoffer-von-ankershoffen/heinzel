import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { app, BrowserWindow, ipcMain } from "electron";
import { lifecycle, parseConnection } from "./connection.js";

const connection = parseConnection(process.argv.slice(app.isPackaged ? 1 : 2));
app.setName("Heinzelmännchen");
let quitting = false;
ipcMain.handle("hz:lifecycle", async (event) => {
  if (!BrowserWindow.fromWebContents(event.sender)) throw new Error("Unknown window.");
  return lifecycle(connection);
});
app.on("before-quit", () => { quitting = true; });
await app.whenReady();
const window = new BrowserWindow({
  width: 1100,
  height: 760,
  show: false,
  webPreferences: {
    preload: fileURLToPath(new URL("./preload.cjs", import.meta.url)),
    contextIsolation: true,
    nodeIntegration: false,
    sandbox: true,
    webviewTag: false,
  },
});
window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
window.webContents.on("will-navigate", (event) => event.preventDefault());
window.on("closed", () => { if (!quitting) app.quit(); });
const html = await readFile(new URL("./index.html", import.meta.url), "utf8");
await window.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(html)}`);
window.show();
