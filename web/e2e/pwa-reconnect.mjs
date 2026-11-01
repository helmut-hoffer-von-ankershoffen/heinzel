// An open app window (an installed PWA stays open for days) while hz restarts on the same port: it must find the new
// server by itself and never ask to sign in again. It may reload itself once, to swap its dead session for its device.
// node pwa-reconnect.mjs ENGINE PROFILE_DIR LINK SIGNAL_DIR
//   writes SIGNAL_DIR/paired once the window is signed in, then waits for SIGNAL_DIR/restarted.
import { existsSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { chromium, webkit } from "playwright-core";

const [engineName, profile, link, signals] = process.argv.slice(2);
const engine = engineName === "webkit" ? webkit : chromium;
const context = await engine.launchPersistentContext(profile, {
  viewport: { width: 1100, height: 800 },
  serviceWorkers: "allow",
});
const signIn = /Sign in again|isn't answering|Open the link/;
try {
  const page = context.pages()[0] ?? (await context.newPage());
  await page.goto(link);
  const ready = page.getByRole("contentinfo");
  await ready.waitFor({ timeout: 30000 });
  await page.waitForFunction(() => Boolean(localStorage.getItem("hz.device")), null, { timeout: 30000 });
  const before = await page.evaluate(async () => {
    const headers = { Authorization: `Bearer ${localStorage.getItem("hz.device")}` };
    return (await (await fetch("/api/lifecycle", { headers })).json()).instance;
  });
  writeFileSync(join(signals, "paired"), before);
  for (let waited = 0; !existsSync(join(signals, "restarted")); waited += 250) {
    if (waited > 120000) throw new Error("the test never restarted hz");
    await page.waitForTimeout(250);
  }
  // The window is not navigated from here on: it has to recover on its own.
  let recovered = false;
  let instance = before;
  for (let waited = 0; waited < 90000 && !recovered; waited += 1000) {
    await page.waitForTimeout(1000);
    const state = await page.evaluate(async () => {
      try {
        const headers = { Authorization: `Bearer ${localStorage.getItem("hz.device")}` };
        const answer = await (await fetch("/api/lifecycle", { headers })).json();
        return { instance: answer.instance };
      } catch {
        return { instance: null };
      }
    });
    instance = state.instance ?? instance;
    recovered = state.instance !== null && state.instance !== before;
  }
  await page.waitForTimeout(3000);
  const asksToSignIn = (await page.getByText(signIn).count()) > 0;
  console.log(
    JSON.stringify({
      recovered,
      newInstance: instance !== before,
      footerShown: (await ready.count()) > 0,
      asksToSignIn,
      text: await page.evaluate(() => document.body.textContent.slice(0, 160)),
    }),
  );
} finally {
  await context.close();
}
