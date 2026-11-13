// A version bump under an open page (heinzel-management-uke3), in Chromium and WebKit on a phone (375px, touch): hz
// is updated elsewhere and now serves another web build. The open window notices without anyone reloading it:
//   1. idle, in front: it reloads itself onto the new build and says so once; it never reloads again for it;
//   2. unsent text, in front: it waits and says why; sent to the background, it saves the text, reloads, and the new
//      page has the text back and says so.
// The page and `/` are served with the current build; every API call is answered locally. The page and the module it
// mounts live here, served by a Vite plugin, so this driver is the one file its journey owns.
// Optional argument: a directory for one screenshot per notice and browser.
import assert from "node:assert/strict";
import { mkdirSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { chromium, webkit } from "playwright-core";
import { createServer } from "vite";

const require = createRequire(import.meta.url);
const axe = readFileSync(require.resolve("axe-core/axe.min.js"), "utf8");
const [shots] = process.argv.slice(2);
if (shots) mkdirSync(shots, { recursive: true });

const status = {
  enabled: true,
  reason: null,
  phase: "idle",
  version: "1.0.0",
  core: null,
  plugins: {},
  seconds_left: null,
  apply_at: null,
  next_window_at: null,
  postponements: 0,
  message: null,
};
const served = (build) =>
  `<!doctype html><html><head><link rel="stylesheet" href="/assets/build-${build}.css"></head><body></body></html>`;

// What an open window has when hz is updated under it: the real chat draft hook and the real automatic-update
// notices. Its own hashed build is build-a; the driver rewrites it to the build hz serves when the page loads.
const PAGE = `<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Stale web build fixture</title>
    <link rel="stylesheet" href="/assets/build-a.css" />
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/@stale-build/page.tsx"></script>
  </body>
</html>`;
const MODULE = `
import { createRoot } from "react-dom/client";
import { Providers, queryClient } from "@/App";
import { AutoUpdate } from "@/updates/AutoUpdate";
import { useUpdateDraft } from "@/updates/draft";
import "@/index.css";

sessionStorage.setItem("hz.token", "fixture-session");

function Composer() {
  const { message, setMessage } = useUpdateDraft("route:/", null);
  return (
    <textarea
      aria-label="Your message"
      className="min-h-24 w-full rounded border p-2"
      value={message}
      onChange={(event) => setMessage(event.target.value)}
    />
  );
}

createRoot(document.getElementById("root")).render(
  <Providers client={queryClient()}>
    <main className="min-h-screen bg-background p-3 text-foreground">
      <h1>Chat</h1>
      <Composer />
    </main>
    <AutoUpdate />
  </Providers>,
);
`;
const fixture = {
  name: "stale-build-fixture",
  resolveId: (id) => (id === "/@stale-build/page.tsx" ? id : null),
  load: (id) => (id === "/@stale-build/page.tsx" ? MODULE : null),
  configureServer(vite) {
    vite.middlewares.use("/e2e/stale-build.html", async (request, response) => {
      response.setHeader("Content-Type", "text/html");
      response.end(await vite.transformIndexHtml(request.originalUrl ?? "/e2e/stale-build.html", PAGE));
    });
  },
};

const server = await createServer({ server: { host: "127.0.0.1", port: 0 }, plugins: [fixture] });
await server.listen();
const address = server.httpServer.address();
assert(address && typeof address !== "string");
const origin = `http://127.0.0.1:${address.port}`;
const results = [];

/** What the window shows now, and whether its notice fits the phone and passes axe. */
async function notice(page, where, text, shot) {
  const card = page.locator("[data-hz-reload]");
  await card.getByText(text).waitFor();
  const fits = await card.evaluate((element) => {
    const box = element.getBoundingClientRect();
    return box.left >= 0 && box.right <= innerWidth && box.top >= 0 && box.bottom <= innerHeight;
  });
  assert.ok(fits, `${where}: the notice fits the phone`);
  await page.evaluate(axe);
  const report = await card.evaluate((element) =>
    window.axe.run(element, {
      runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"] },
    }),
  );
  assert.deepEqual(
    report.violations.map((item) => item.id),
    [],
    `${where}: accessibility`,
  );
  if (shots) await page.screenshot({ path: `${shots}/stale-build-${shot}.png` });
}

try {
  for (const [name, engine] of [
    ["chromium", chromium],
    ["webkit", webkit],
  ]) {
    const browser = await engine.launch();
    try {
      for (const scenario of ["idle", "draft"]) {
        const where = `${name} ${scenario}`;
        const context = await browser.newContext({
          viewport: { width: 375, height: 760 },
          isMobile: true,
          hasTouch: true,
        });
        const page = await context.newPage();
        const pageErrors = [];
        const unknown = [];
        page.on("pageerror", (error) => pageErrors.push(String(error)));
        let build = "a";
        let loads = 0;
        // The page and `/` as hz serves them now; the build's stylesheet itself is empty.
        await page.route(`${origin}/e2e/stale-build.html`, async (route) => {
          loads += 1;
          const response = await route.fetch();
          const body = (await response.text()).replace("/assets/build-a.css", `/assets/build-${build}.css`);
          await route.fulfill({ response, body });
        });
        await page.route(`${origin}/`, (route) =>
          route.fulfill({ contentType: "text/html", body: served(build), headers: { "Cache-Control": "no-store" } }),
        );
        await page.route(/\/assets\/build-[a-z]\.css$/, (route) =>
          route.fulfill({ contentType: "text/css", body: "" }),
        );
        await page.route(
          (url) => url.pathname.startsWith("/api/"),
          (route) => {
            const path = new URL(route.request().url()).pathname;
            if (path === "/api/auto-update") return route.fulfill({ json: status });
            // The automatic-update switch reads its setting: on, as by default.
            if (path === "/api/system/config/show")
              return route.fulfill({
                json: { settings: [{ section: "hz", key: "auto_update", value: true, source: "default" }] },
              });
            unknown.push(path);
            return route.fulfill({ status: 404, json: { error: "not in this fixture" } });
          },
        );
        await page.goto(`${origin}/e2e/stale-build.html`);
        const composer = page.getByRole("textbox", { name: "Your message" });
        await composer.waitFor();
        // A first check finds the page current: nothing happens.
        await page.waitForTimeout(500);
        assert.equal(loads, 1, `${where}: no reload while the page runs the served build`);
        if (scenario === "draft") await composer.fill("Half a thought");
        // hz is updated elsewhere: from now on it serves build b. The window comes to the front, as a phone's does.
        build = "b";
        const reloaded = page.waitForEvent("load");
        await page.evaluate(() => window.dispatchEvent(new Event("focus")));
        if (scenario === "draft") {
          await notice(page, where, /Reload to finish the update/, `${name}-held`);
          assert.equal(loads, 1, `${where}: unsent text in front holds the reload`);
          // The person switches away: the window saves the text and reloads.
          await page.evaluate(() => {
            Object.defineProperty(document, "visibilityState", { value: "hidden", configurable: true });
            document.dispatchEvent(new Event("visibilitychange"));
          });
        }
        await reloaded;
        assert.equal(loads, 2, `${where}: one reload`);
        await notice(page, where, "Heinzel was updated, so this window reloaded.", `${name}-${scenario}-reloaded`);
        const kept = page.getByText("Your unsent message is still here.");
        if (scenario === "draft") {
          await kept.waitFor();
          assert.equal(await composer.inputValue(), "Half a thought", `${where}: the unsent text is back`);
        } else assert.equal(await kept.count(), 0, `${where}: no draft to speak of`);
        // The reloaded page runs build b: asking again changes nothing.
        await page.evaluate(() => window.dispatchEvent(new Event("focus")));
        await page.waitForTimeout(500);
        assert.equal(loads, 2, `${where}: no second reload`);
        const ok = page.getByRole("button", { name: "OK" });
        await ok.tap();
        await ok.waitFor({ state: "detached" });
        assert.deepEqual(pageErrors, [], `${where}: page errors`);
        assert.deepEqual(unknown, [], `${where}: requests outside the fixture`);
        results.push({ browser: name, scenario, reloads: loads - 1 });
        await context.close();
      }
    } finally {
      await browser.close();
    }
  }
  console.log(JSON.stringify(results));
} finally {
  await server.close();
}
