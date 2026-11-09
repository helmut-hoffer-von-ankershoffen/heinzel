// A paired browser reopens the app without a link: a persistent browser profile, one step per run.
// node device-reopen.mjs ENGINE PROFILE_DIR STEP URL [stale]   STEP: pair | open | restart

import { chromium, webkit } from "playwright-core";
import { explainOwnedBrowser } from "./reopen-processes.mjs";
import { until } from "./until.mjs";

const [engineName, profile, step, url, mode] = process.argv.slice(2);
const engine = engineName === "webkit" ? webkit : chromium;
// An installed app window shares this profile's storage with the browser, so a fresh context on the profile stands in.
// What happened when, printed on stderr, which the test shows only when this run fails. On macOS CI a reopened WebKit
// window sometimes never reached "load" (#455); the timeline and the probes below say whether the server answered,
// what the document reached, and which requests were still open.
const began = Date.now();
const note = (event) => console.error(`${String(Date.now() - began).padStart(6)} ms  ${event}`);
const path = (request) => {
  const address = new URL(request.url());
  return `${request.method()} ${address.origin === new URL(url).origin ? address.pathname : address.origin}`;
};
note(`launch ${engineName} ${step}${mode ? ` ${mode}` : ""}`);
const context = await engine.launchPersistentContext(profile, {
  viewport: { width: 1100, height: 800 },
  serviceWorkers: "allow",
});
note("launched");
const open = new Set();
context.on("request", (request) => {
  open.add(request);
  note(`request ${path(request)}${request.isNavigationRequest() ? " (navigation)" : ""}`);
});
context.on("response", (response) => {
  const request = response.request();
  if (request.isNavigationRequest()) {
    note(`response ${path(request)} ${response.status()}${response.fromServiceWorker() ? " from the worker" : ""}`);
  }
});
context.on("requestfinished", async (request) => {
  open.delete(request);
  const response = await request.response().catch(() => null);
  note(
    `finished ${path(request)} ${response?.status() ?? "?"}${response?.fromServiceWorker() ? " from the worker" : ""}`,
  );
});
context.on("requestfailed", (request) => {
  open.delete(request);
  note(`failed ${path(request)} ${request.failure()?.errorText ?? ""}`);
});
context.on("page", (page) => watchPage(page));
for (const page of context.pages()) watchPage(page);
function watchPage(page) {
  page.on("domcontentloaded", () => note("domcontentloaded"));
  page.on("load", () => note("load"));
  page.on("framenavigated", (frame) => {
    if (frame === page.mainFrame()) note(`navigated ${frame.url().split("#")[0]}`);
  });
  page.on("crash", () => note("page crashed"));
}
const within = (promise, ms) =>
  Promise.race([promise, new Promise((resolve) => setTimeout(() => resolve(`no answer in ${ms} ms`), ms))]);
async function explain(page) {
  note(`still open: ${[...open].map(path).join(", ") || "none"}`);
  for (const request of open) {
    if (request.isNavigationRequest()) note(`navigation timing ${path(request)} ${JSON.stringify(request.timing())}`);
  }
  const server = await fetch(new URL("/", url), { signal: AbortSignal.timeout(5000) }).then(
    (answer) => `server answers / with ${answer.status}`,
    (error) => `server does not answer /: ${error.message}`,
  );
  note(server);
  const documentState = await within(
    page
      .evaluate(() => ({
        location: location.pathname,
        readyState: document.readyState,
        worker: navigator.serviceWorker?.controller?.state ?? "none",
        pending: performance
          .getEntriesByType("resource")
          .filter((entry) => entry.responseEnd === 0)
          .map((entry) => new URL(entry.name).pathname),
      }))
      .then(JSON.stringify, (error) => `the page does not evaluate: ${error.message}`),
    5000,
  );
  note(`document ${documentState}`);
}
// Closing a persistent context ends the browser at once, also in the middle of the worker's install (`cache.addAll`) or
// of the copies it keeps of the page's assets (`cache.put`). WebKit can then hold the next launch's first navigation:
// the server answers it, the page never gets the response (beads zd4, #455; Playwright issue 42273 ties such stalls to
// worker cache writes). A person does not quit the browser within a second of the page loading, so each step ends
// only after the worker is active and every asset the controlled page loaded is cached: no write is cut off.
async function settleWorker(page) {
  await until(
    () =>
      page.evaluate(async () => {
        const registration = await navigator.serviceWorker?.getRegistration("/");
        if (registration?.active?.state !== "activated" || registration.installing || registration.waiting)
          return false;
        if (!(await caches.match("/offline.html"))) return false;
        // Only what went through the worker is copied: not the assets of a page it took over after they loaded.
        const assets = performance
          .getEntriesByType("resource")
          .filter((entry) => entry.workerStart > 0)
          .map((entry) => new URL(entry.name))
          .filter((address) => address.origin === location.origin && address.pathname.startsWith("/assets/"));
        const kept = await Promise.all(assets.map((address) => caches.match(address.href)));
        return kept.every(Boolean);
      }),
    { timeout: 20000, interval: 200, message: "The service worker never settled: active, with its copies written" },
  );
  note("worker settled");
}
let current;
try {
  if (mode === "stale" && step === "open") {
    // A window that outlived `hz ui` (an installed app restores its session) still holds the old session token.
    await context.addInitScript(() => sessionStorage.setItem("hz.token", "dead-session-token-of-an-earlier-hz-ui"));
  }
  const page = context.pages()[0] ?? (await context.newPage());
  current = page;
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
  let afterRestart;
  if (step === "restart") {
    // The app's own Restart: `POST /api/restart` re-executes hz in place and hands its token over. Wait until another
    // instance answers, then open the app again from the same profile without a link.
    const instance = await page.evaluate(async () => {
      const headers = { Authorization: `Bearer ${localStorage.getItem("hz.device")}` };
      const before = (await (await fetch("/api/lifecycle", { headers })).json()).instance;
      await fetch("/api/restart", { method: "POST", headers }).catch(() => undefined);
      return before;
    });
    await until(
      () =>
        page.evaluate(async (old) => {
          try {
            const headers = { Authorization: `Bearer ${localStorage.getItem("hz.device")}` };
            const answer = await (await fetch("/api/lifecycle", { headers })).json();
            return answer.instance && answer.instance !== old;
          } catch {
            return false;
          }
        }, instance),
      { timeout: 60_000, interval: 500, message: "A new instance did not answer after the restart" },
    );
    await page.goto(url);
    await Promise.race([ready.waitFor({ timeout: 20000 }), failed.first().waitFor({ timeout: 20000 })]).catch(
      () => undefined,
    );
    await page.waitForTimeout(500);
    afterRestart = (await ready.count()) > 0 && (await failed.count()) === 0;
  }
  await settleWorker(page);
  console.log(JSON.stringify({ step, signedIn, afterRestart, ...stored }));
} catch (error) {
  if (current) await explain(current);
  await explainOwnedBrowser(note);
  throw error;
} finally {
  await context.close();
}
