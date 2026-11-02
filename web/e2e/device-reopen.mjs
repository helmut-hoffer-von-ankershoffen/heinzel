// A paired browser reopens the app without a link: a persistent browser profile, one step per run.
// node device-reopen.mjs ENGINE PROFILE_DIR STEP URL [stale]   STEP: pair | open | restart

import { chromium, webkit } from "playwright-core";
import { sampleDiagnosticBrowser } from "./process-samples.mjs";
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
  await sampleDiagnosticBrowser(note);
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
  console.log(JSON.stringify({ step, signedIn, afterRestart, ...stored }));
} catch (error) {
  if (current) await explain(current);
  throw error;
} finally {
  await context.close();
}
