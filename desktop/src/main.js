// The desktop's main process: build the pieces, then take the single-instance lock and start (see the main-*.js files).
import { app, dialog, protocol } from "electron";
import { initializeDesktopIdentity } from "./identity.js";
import { installSignalQuit } from "./signal-quit.js";
import { registerIpc } from "./main-ipc.js";
import { registerQuitHandler } from "./main-quit.js";
import { watchLocalService } from "./main-service-watch.js";
import { createServices } from "./main-services.js";
import { startWhenReady } from "./main-startup.js";
import { createWindowLifecycle } from "./main-window.js";
import { ctx } from "./main-state.js";

protocol.registerSchemesAsPrivileged([{ scheme: "heinzel-album", privileges: { standard: true, secure: true, stream: true, supportFetchAPI: true } }]);

const identityReady = initializeDesktopIdentity(app, dialog);
ctx.signalQuit = installSignalQuit({
  proc: process,
  quit: () => app.quit(),
  forceExit: (code) => app.exit(code),
});
ctx.instance = createWindowLifecycle();
createServices();
if (identityReady && ctx.instance.acquire(process.argv.slice(app.isPackaged ? 1 : 2))) {
  registerIpc();
  watchLocalService();
  registerQuitHandler();
  startWhenReady();
}
