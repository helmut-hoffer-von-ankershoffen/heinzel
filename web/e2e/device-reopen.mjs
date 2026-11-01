// A paired browser reopens the app without a link: a persistent browser profile, one step per run.
// node device-reopen.mjs ENGINE PROFILE_DIR STEP URL [stale]   STEP: pair | open
import { chromium, webkit } from "playwright-core";

const [engineName, profile, step, url, mode] = process.argv.slice(2);
const engine = engineName === "webkit" ? webkit : chromium;
// An installed app window shares this profile's storage with the browser, so a fresh context on the profile stands in.
const context = await engine.launchPersistentContext(profile, {
  viewport: { width: 1100, height: 800 },
  serviceWorkers: "allow",
});
try {
  if (mode === "stale" && step === "open") {
    // A window that outlived `hz ui` (an installed app restores its session) still holds the old session token.
    await context.addInitScript(() => sessionStorage.setItem("hz.token", "dead-session-token-of-an-earlier-hz-ui"));
  }
  const page = context.pages()[0] ?? (await context.newPage());
  await page.goto(url);
  const failed = page.getByText(/Sign in again|isn't answering|Open the link/);
  const ready = page.getByRole("contentinfo");
  await Promise.race([ready.waitFor({ timeout: 20000 }), failed.first().waitFor({ timeout: 20000 })]).catch(
    () => undefined,
  );
  await page.waitForTimeout(500);
  const signedIn = (await ready.count()) > 0 && (await failed.count()) === 0;
  const stored = await page.evaluate(() => ({
    device: Boolean(localStorage.getItem("hz.device")),
    standalone: matchMedia("(display-mode: standalone)").matches,
    heading: document.body.textContent.slice(0, 160),
  }));
  if (step === "pair" && !stored.device) {
    await page
      .waitForFunction(() => Boolean(localStorage.getItem("hz.device")), null, { timeout: 15000 })
      .catch(() => undefined);
    stored.device = await page.evaluate(() => Boolean(localStorage.getItem("hz.device")));
  }
  console.log(JSON.stringify({ step, signedIn, ...stored }));
} finally {
  await context.close();
}
