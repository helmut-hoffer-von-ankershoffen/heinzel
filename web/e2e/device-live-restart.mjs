// An open app window outlives a daemon restart on the same port: it reconnects by itself and never asks to sign in.
// node device-live-restart.mjs ENGINE PROFILE_DIR LINK ORIGIN
// Talks to its parent on stdout/stdin, one line each: READY (signed in) / "stopped" / DOWN / "started" -> final JSON.

import { createInterface } from "node:readline";
import { chromium, webkit } from "playwright-core";
import { until } from "./until.mjs";

const [engineName, profile, link, origin] = process.argv.slice(2);
const engine = engineName === "webkit" ? webkit : chromium;
const input = createInterface({ input: process.stdin });
const lines = input[Symbol.asyncIterator]();
const next = async (expected) => {
  const { value } = await lines.next();
  if (value?.trim() !== expected) throw new Error(`expected "${expected}" from the parent, got "${value}"`);
};

const context = await engine.launchPersistentContext(profile, {
  viewport: { width: 1100, height: 800 },
  serviceWorkers: "allow",
});
try {
  const page = context.pages()[0] ?? (await context.newPage());
  const failed = page.getByText(/Sign in again|Open the link/);
  const ready = page.getByRole("contentinfo");
  const signedIn = async () => (await ready.count()) > 0 && (await failed.count()) === 0;
  const lifecycle = () =>
    page.evaluate(async () => {
      try {
        const headers = { Authorization: `Bearer ${localStorage.getItem("hz.device")}` };
        const answer = await fetch("/api/lifecycle", { headers, cache: "no-store" });
        return answer.ok ? (await answer.json()).instance : null;
      } catch {
        return null;
      }
    });

  await page.goto(link);
  await ready.waitFor({ timeout: 20000 });
  await until(() => page.evaluate(() => Boolean(localStorage.getItem("hz.device"))), {
    timeout: 15000,
    message: "The device was never paired",
  });
  // The pairing link is gone from the address, as in an installed window that was opened once.
  await page.goto(origin);
  await ready.waitFor({ timeout: 20000 });
  const before = await lifecycle();
  console.log("READY");

  await next("stopped");
  await until(async () => (await lifecycle()) === null, {
    timeout: 20000,
    message: "The stopped daemon still answers",
  });
  console.log("DOWN");

  await next("started");
  // No navigation from here on: the window that stayed open has to find the new daemon on its own.
  const after = await until(lifecycle, { timeout: 60000, interval: 500, message: "No daemon answered after restart" });
  await until(
    async () => {
      await page.waitForTimeout(0);
      return (await signedIn()) && !(await page.getByText(/isn't answering|Reconnecting/i).count());
    },
    { timeout: 60000, interval: 500, message: "The open window did not recover without a reload" },
  );
  const stayedSignedIn = await signedIn();
  await page.waitForTimeout(1500);
  const askedToSignIn = (await failed.count()) > 0;

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
