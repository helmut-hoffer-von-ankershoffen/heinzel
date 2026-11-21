// The application menu: it only asks the page to open About, Settings and Updates.
import { BrowserWindow, Menu, app, shell } from "electron";
import { appMenuTemplate, helpLogLine, isAllowedHelpUrl, menuLogLine, menuScript } from "./app-menu.js";
import { showConnection } from "./main-picker.js";
import { productWindows } from "./main-state.js";

export function installAppMenu() {
    // The native About panel is never the target (the menu opens the web dialog), but if macOS shows it anyway it
    // names the app properly and shows one version, not "Version X (X)".
    app.setAboutPanelOptions({ applicationName: app.name, applicationVersion: app.getVersion(), version: "" });
    // The page owns what About, Settings and Updates show; the menu only asks it to open them.
    Menu.setApplicationMenu(Menu.buildFromTemplate(appMenuTemplate({
      name: app.name,
      devTools: !app.isPackaged,
      open: (url) => {
        const allowed = isAllowedHelpUrl(url);
        console.info(helpLogLine(url, allowed));
        if (allowed) void shell.openExternal(url);
      },
      send: (action) => {
        if (action === "connection") { showConnection(); return; }
        // Close Tab acts on the focused window only: with none focused it must never close a background window.
        const window = action === "close-tab" ? BrowserWindow.getFocusedWindow()
          : BrowserWindow.getFocusedWindow() ?? BrowserWindow.getAllWindows()[0];
        const ready = Boolean(window && productWindows.has(window) && !window.isDestroyed());
        console.info(menuLogLine(action, ready ? "sent" : "no_window"));
        if (!ready) return;
        const sent = window.webContents.executeJavaScript(menuScript(action));
        // Cmd+W closes the browser's selected tab when the page takes it, and otherwise the window as before.
        if (action === "close-tab") void sent.then((taken) => {
          console.info(menuLogLine(action, taken === true ? "taken" : "not_taken"));
          if (taken !== true && !window.isDestroyed()) window.close();
        }, () => {});
        else void sent;
      },
    })));
}
