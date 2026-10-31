import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "@/App";
import { dropStaleSession, pairDevice } from "@/api/device";
import { adoptToken, deviceToken, sessionToken } from "@/api/token";
import { applyTheme } from "@/layout/theme";
import { captureInstallPrompt } from "@/pwa/install";
import { registerServiceWorker } from "@/pwa/register";
import { splash } from "@/splash/store";
import "./index.css";

applyTheme();
captureInstallPrompt(); // before the browser fires it, so "Install app" can offer it later
let session = adoptToken();
// With the link `hz ui` opened, pair this browser once, so the app reopens from the Dock without one (ADR 0046); the
// splash says "Signing you in…" meanwhile.
splash.send({ type: "pairing" });
await dropStaleSession(); // a window that outlived `hz ui` carries a dead session token: use the device token instead
session = sessionToken();
void pairDevice().finally(() => splash.send({ type: "paired" }));
window.addEventListener("load", () => void registerServiceWorker());
// A new `#token=…` (e.g. after `hz ui` restarted) replaces the stored one and starts the app afresh.
window.addEventListener("hashchange", () => {
  if (new URLSearchParams(window.location.hash.replace(/^#/, "")).has("token")) {
    adoptToken();
    window.location.reload();
  }
});
const root = document.getElementById("root");
if (root) {
  createRoot(root).render(
    <StrictMode>
      <App hasToken={session !== null || deviceToken() !== null} />
    </StrictMode>,
  );
}
