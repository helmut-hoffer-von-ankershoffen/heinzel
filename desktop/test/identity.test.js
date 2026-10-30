import assert from "node:assert/strict";
import test from "node:test";
import { configureDesktopIdentity, initializeDesktopIdentity } from "../src/identity.js";

function fixture(suffix = " Chrome/156.0.8078.48 Electron/45.0.0 Safari/537.36") {
  let name = "heinzel-desktop";
  let fallback;
  const prefix = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 ";
  return {
    setName(value) { name = value; },
    get name() { return name; },
    get userAgentFallback() { return fallback ?? prefix + name + "/0.7.28" + suffix; },
    set userAgentFallback(value) { fallback = value; },
  };
}

test("the visible native name stays Unicode while HTTP identity preserves the platform and browser versions", () => {
  const app = fixture();
  configureDesktopIdentity(app);
  assert.equal(app.name, "Heinzelmännchen");
  assert.equal(app.userAgentFallback,
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Heinzelmaennchen/0.7.28 Chrome/156.0.8078.48 Electron/45.0.0 Safari/537.36");
  assert.match(app.userAgentFallback, /^[\x20-\x7e]+$/);
});

test("an unexpectedly invalid fallback cannot proceed or expose header content in diagnostics", () => {
  const app = fixture(" private\ninvalid");
  assert.throws(() => configureDesktopIdentity(app), { message: "The desktop HTTP identity could not be initialized." });
});

test("an invalid identity shows a safe error and prevents startup", () => {
  const app = fixture(" private\ninvalid");
  let quit = false;
  let message;
  app.quit = () => { quit = true; };
  const dialog = { showErrorBox(title, text) { message = `${title}: ${text}`; } };
  assert.equal(initializeDesktopIdentity(app, dialog), false);
  assert.equal(quit, true);
  assert.match(message, /could not initialize its browser identity/);
  assert.doesNotMatch(message, /private|invalid/);
});

test("an unavailable error dialog still prevents startup", () => {
  const app = fixture(" invalid\nheader");
  let quit = false;
  app.quit = () => { quit = true; };
  const dialog = { showErrorBox() { throw new Error("dialog unavailable"); } };
  assert.equal(initializeDesktopIdentity(app, dialog), false);
  assert.equal(quit, true);
});
