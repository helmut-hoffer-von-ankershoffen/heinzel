// An open app window outlives a daemon restart on the same port: it reconnects by itself and never asks to sign in.
// node device-live-restart.mjs ENGINE PROFILE_DIR LINK ORIGIN
// Talks to its parent on stdout/stdin, one line each: READY (signed in) / "stopped" / DOWN / "started" -> final JSON.

import { createInterface } from "node:readline";
import { chromium, webkit } from "playwright-core";
import { steady } from "./steady.mjs";
import { until } from "./until.mjs";

const [engineName, profile, link, origin] = process.argv.slice(2);
const engine = engineName === "webkit" ? webkit : chromium;
const input = createInterface({ input: process.stdin });
const lines = input[Symbol.asyncIterator]();
const next = async (expected) => {
  const { value } = await lines.next();
  if (value?.trim() !== expected) throw new Error(`expected "${expected}" from the parent, got "${value}"`);
};

let watching = false;
let askedToSignIn = false;
const context = await engine.launchPersistentContext(profile, {
  viewport: { width: 1100, height: 800 },
  serviceWorkers: "allow",
});
// Every document the window loads reports a sign-in prompt the moment one appears, so a prompt that only flashes during
// the window's own reload is seen too, without sampling the page at a fixed time.
await context.exposeBinding("hzAskedToSignIn", () => {
  if (watching) askedToSignIn = true;
});
await context.addInitScript(() => {
  const look = () => {
    if (/Sign in again|Open the link/.test(document.body?.textContent ?? "")) window.hzAskedToSignIn();
  };
  new MutationObserver(look).observe(document, { childList: true, subtree: true, characterData: true });
});
try {
  const page = context.pages()[0] ?? (await context.newPage());
  const failed = page.getByText(/Sign in again|Open the link/);
  const ready = page.getByRole("contentinfo");
  const signedIn = async () =>
    Boolean(await steady(async () => (await ready.count()) > 0 && (await failed.count()) === 0));
  // The page's own session token: a window that outlived `hz ui` drops it once the new daemon refuses it, then reloads.
  const sessionToken = () => steady(() => page.evaluate(() => sessionStorage.getItem("hz.token")));
  let device = null;
  // Asked from Node with the paired device's token, so the answer doesn't depend on the page staying loaded meanwhile.
  const lifecycle = async () => {
    try {
      const answer = await context.request.get(new URL("/api/lifecycle", origin).href, {
        headers: { Authorization: `Bearer ${device}` },
        timeout: 5000,
      });
      return answer.ok() ? (await answer.json()).instance : null;
    } catch {
      return null;
    }
  };

  await page.goto(link);
  await ready.waitFor({ timeout: 20000 });
  device = await until(() => steady(() => page.evaluate(() => localStorage.getItem("hz.device"))), {
    timeout: 15000,
    message: "The device was never paired",
  });
  // The pairing link is gone from the address, as in an installed window that was opened once.
  await page.goto(origin);
  await ready.waitFor({ timeout: 20000 });
  if (!(await sessionToken())) throw new Error("The window opened without a session token to outlive");
  const before = await lifecycle();
  console.log("READY");

  await next("stopped");
  watching = true;
  await until(async () => (await lifecycle()) === null, {
    timeout: 20000,
    message: "The stopped daemon still answers",
  });
  console.log("DOWN");

  await next("started");
  // No navigation from here on: the window that stayed open has to find the new daemon on its own.
  const after = await until(lifecycle, { timeout: 60000, interval: 100, message: "No daemon answered after restart" });
  // Recovered means: the window dropped the session token the new daemon refused, and shows the app signed in
  // with nothing about reconnecting. Polled through the window's own reload, which is what this waits for.
  await until(
    async () =>
      (await sessionToken()) === null &&
      (await signedIn()) &&
      (await steady(() => page.getByText(/isn't answering|Reconnecting/i).count())) === 0,
    { timeout: 60000, interval: 100, message: "The open window did not recover without a reload" },
  );
  const stayedSignedIn = await signedIn();

  // Reopening the app window from the same profile, with no link, does not ask to sign in either.
  await page.goto(origin);
  await Promise.race([ready.waitFor({ timeout: 20000 }), failed.first().waitFor({ timeout: 20000 })]).catch(
    () => undefined,
  );
  const reopened = await signedIn();
  console.log(
    JSON.stringify({
      restarted: Boolean(before && after && before !== after),
      stayedSignedIn,
      askedToSignIn,
      reopened,
    }),
  );
} finally {
  input.close();
  await context.close();
}
