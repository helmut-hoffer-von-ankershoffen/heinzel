// An installed window opened while hz is down shows the offline page, and opens the app by itself once hz is back:
// no reload, navigation or restart from the person (here: from this driver) after hz starts again.
// node pwa-reconnect.mjs ENGINE PROFILE_DIR LINK ORIGIN
// Talks to its parent on stdout/stdin: READY / "stopped" / DOWN / "started" -> final JSON.
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
const began = Date.now();
const note = (event) => console.error(`${String(Date.now() - began).padStart(6)} ms  ${event}`);
const context = await engine.launchPersistentContext(profile, {
  // A phone: the installed window this guards is an iPhone's, opened over the tailnet.
  viewport: { width: 390, height: 700 },
  isMobile: true,
  hasTouch: true,
  serviceWorkers: "allow",
});
// What an older build left on the phone: its shell cache under the constant name every build used before
// (hz-shell-v4), with its own offline page, and the version its splash remembered.
const STALE_OFFLINE = "<p>hz v0.7.27 is down. Run hz old-command.</p>";
try {
  const page = context.pages()[0] ?? (await context.newPage());
  page.on("console", (message) => message.type() === "error" && note(`console error: ${message.text()}`));
  const ready = page.getByRole("contentinfo");

  // Planted on a page of the origin that registers no worker, before this build's worker installs.
  await page.goto(new URL("/manifest.webmanifest", origin).href);
  await page.evaluate(async (stale) => {
    const old = await caches.open("hz-shell-v4");
    await old.put("/offline.html", new Response(stale, { headers: { "content-type": "text/html" } }));
    await old.put("/old-build-marker", new Response("old"));
    localStorage.setItem("hz.splash", JSON.stringify({ version: "0.7.30" }));
  }, STALE_OFFLINE);
  await page.goto(link);
  await ready.waitFor({ timeout: 20000 });
  await until(() => page.evaluate(() => localStorage.getItem("hz.device")), {
    timeout: 15000,
    message: "The device was never paired",
  });
  // Open the installed window only once the worker is active with the offline page kept. A navigation that starts
  // while the worker is still installing is not intercepted, and clients.claim() at activation can miss a document
  // still being created: that page stays uncontrolled for its lifetime, which no wait below could change.
  await until(
    () =>
      page.evaluate(async () => {
        const registration = await navigator.serviceWorker.getRegistration("/");
        return registration?.active?.state === "activated" && Boolean(await caches.match("/offline.html"));
      }),
    { timeout: 20000, message: "The service worker never activated with the offline page cached" },
  );
  await page.goto(origin); // the pairing link is gone from the address, as in an installed window
  await ready.waitFor({ timeout: 20000 });
  // The worker controls the page and has kept the offline page.
  await until(
    () =>
      page.evaluate(
        async () => Boolean(navigator.serviceWorker.controller) && Boolean(await caches.match("/offline.html")),
      ),
    { timeout: 20000, message: "The service worker never took control with the offline page cached" },
  );
  // The worker is this build's, its shell is this build's copy, and no older build's cache is left to answer from.
  const shell = await page.evaluate(async () => {
    const worker = await (await fetch("/sw.js", { cache: "no-store" })).text();
    const build = /^const BUILD = "([0-9a-f]{16})";$/m.exec(worker)?.[1] ?? null;
    const names = (await caches.keys()).filter((name) => name.startsWith("hz-shell-"));
    const own = build ? await caches.open(`hz-shell-${build}`) : null;
    const kept = await own?.match("/offline.html");
    const served = await (await fetch("/offline.html", { cache: "no-store" })).text();
    return {
      build,
      names,
      offlineIsCurrent: kept ? (await kept.text()) === served : false,
      oldMarker: Boolean(await caches.match("/old-build-marker")),
    };
  });
  note(`shell ${JSON.stringify(shell)}`);
  console.log("READY");

  await next("stopped");
  note("hz stopped");
  // The window is opened again while hz is down, as after a reboot.
  await page.reload({ waitUntil: "domcontentloaded" }).catch(() => undefined);
  const status = page.getByRole("status");
  await status.filter({ hasText: /isn't answering/ }).waitFor({ timeout: 30000 });
  const offline = await page.evaluate(() => ({
    text: document.body.innerText,
    icon: document.images[0]?.complete && document.images[0].naturalWidth > 0,
  }));
  await page.evaluate(() => {
    window.hzOfflineMarker = true; // gone once the page reloads by itself
  });
  note("the offline page is up");
  console.log("DOWN");

  await next("started");
  note("hz started again");
  // No navigation from here on: the window has to find hz on its own and open the app.
  await ready.waitFor({ timeout: 90000 });
  const reloaded = await page.evaluate(() => window.hzOfflineMarker === undefined);
  const failed = await page.getByText(/Sign in again|Open the link/).count();
  console.log(
    JSON.stringify({
      offlineShowed: /hz ui serve/.test(offline.text),
      noVersionShown: !/\d+\.\d+\.\d+/.test(offline.text),
      noStaleOfflinePage: !/old-command|0\.7\.27/.test(offline.text),
      workerIsStamped: shell.build !== null,
      onlyThisBuildsCache:
        shell.build !== null && JSON.stringify(shell.names) === JSON.stringify([`hz-shell-${shell.build}`]),
      cachedOfflineIsCurrent: shell.offlineIsCurrent,
      oldBuildCacheDropped: !shell.oldMarker,
      iconLoaded: offline.icon === true,
      openedByItself: reloaded,
      askedToSignIn: failed > 0,
    }),
  );
} finally {
  input.close();
  await context.close();
}
