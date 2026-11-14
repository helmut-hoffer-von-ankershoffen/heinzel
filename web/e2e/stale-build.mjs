// A version bump under an open page (heinzel-management-uke3), in Chromium and WebKit on a phone (375px, touch): hz
// is updated elsewhere and now serves another web build. The open window notices without anyone reloading it:
//   idle   in front, nothing open: it reloads itself onto the new build, says so once, and never reloads again for it;
//   chat   an unsent message in the durable chat composer: it waits in front and says why; in the background it
//          reloads, the new page has the message back from the composer and only then says it is still there;
//   field  text in a field nothing saves: it waits in front and in the background, and reloads once it is cleared;
//   flap   hz flips between two builds: the window reloads twice, then offers Reload instead of looping.
// The page and `/` are served with the current build; every API call is answered locally. The page and the module it
// mounts live here, served by a Vite plugin, so this driver is the one file its journey owns. Every step awaits the
// one before on purpose: one browser, one page, one reload at a time.
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

// What an open window has when hz is updated under it: the durable chat composer, a field nothing saves (a skill's
// name, say) and the real automatic-update cards. Its own hashed build is build-a; the driver rewrites it to the
// build hz serves when the page loads.
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
import { useDurableDraft } from "@/chat/durableDraft";
import { AutoUpdate } from "@/updates/AutoUpdate";
import "@/index.css";

sessionStorage.setItem("hz.token", "fixture-session");

function Composer() {
  const { state, update } = useDurableDraft("hz-01ARZ3NDEKTSV4RRFFQ69G5FAV");
  return (
    <textarea
      aria-label="Your message"
      data-hz-restorable=""
      className="min-h-24 w-full rounded border p-2"
      value={state.text}
      onChange={(event) => update({ text: event.target.value })}
    />
  );
}

createRoot(document.getElementById("root")).render(
  <Providers client={queryClient()}>
    <main className="min-h-screen space-y-2 bg-background p-3 text-foreground">
      <h1>Chat</h1>
      <Composer />
      <input aria-label="Skill name" className="min-h-11 w-full rounded border px-2" />
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

/** The card the window shows, checked to fit the phone and pass axe; one screenshot of it. */
async function card(page, where, text, shot) {
  const output = page.locator("[data-hz-reload]");
  await output.getByText(text).waitFor();
  const fits = await output.evaluate((element) => {
    const box = element.getBoundingClientRect();
    return box.left >= 0 && box.right <= innerWidth && box.top >= 0 && box.bottom <= innerHeight;
  });
  assert.ok(fits, `${where}: the card fits the phone`);
  await page.evaluate(axe);
  const report = await output.evaluate((element) =>
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

/** The window goes to the background, as a phone's does when another app comes up. */
const toBackground = (page) =>
  page.evaluate(() => {
    Object.defineProperty(document, "visibilityState", { value: "hidden", configurable: true });
    document.dispatchEvent(new Event("visibilitychange"));
  });
/** hz answers again after a restart: every window asks at once. */
const backOnline = (page) => page.evaluate(() => window.dispatchEvent(new Event("online")));

/** One window in a fresh tab; `state.build` is what hz serves, and `state.loads` counts page loads. */
async function open(browser, state) {
  const context = await browser.newContext({ viewport: { width: 375, height: 760 }, isMobile: true, hasTouch: true });
  const page = await context.newPage();
  state.errors = [];
  state.unknown = [];
  page.on("pageerror", (error) => state.errors.push(String(error)));
  await page.route(`${origin}/e2e/stale-build.html`, async (route) => {
    state.loads += 1;
    const response = await route.fetch();
    const body = (await response.text()).replace("/assets/build-a.css", `/assets/build-${state.build}.css`);
    await route.fulfill({ response, body });
  });
  await page.route(`${origin}/`, (route) =>
    route.fulfill({ contentType: "text/html", body: served(state.build), headers: { "Cache-Control": "no-store" } }),
  );
  await page.route(/\/assets\/build-[a-z]\.css$/, (route) => route.fulfill({ contentType: "text/css", body: "" }));
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
      state.unknown.push(path);
      return route.fulfill({ status: 404, json: { error: "not in this fixture" } });
    },
  );
  await page.goto(`${origin}/e2e/stale-build.html`);
  await page.getByRole("textbox", { name: "Your message" }).waitFor();
  return { context, page };
}

/** Hz serves `build` from now on; the window asks and, unless held, reloads onto it. */
async function update(page, state, build) {
  state.build = build;
  const loaded = page.waitForEvent("load");
  await backOnline(page);
  return loaded;
}

const scenarios = {
  async idle(page, state, where, name) {
    await update(page, state, "b");
    assert.equal(state.loads, 2, `${where}: one reload`);
    await card(page, where, "Heinzel was updated, so this window reloaded.", `${name}-idle-reloaded`);
    assert.equal(await page.getByText("Your unsent message is still here.").count(), 0, `${where}: no draft claimed`);
    await backOnline(page); // the reloaded page runs build b: nothing more happens
    await page.waitForTimeout(1500);
    assert.equal(state.loads, 2, `${where}: no second reload`);
    await page.getByRole("button", { name: "OK" }).tap();
  },
  async chat(page, state, where, name) {
    const composer = page.getByRole("textbox", { name: "Your message" });
    await composer.fill("Half a thought");
    const reloaded = update(page, state, "b");
    await card(page, where, /Reload to finish the update/, `${name}-chat-held`);
    assert.equal(state.loads, 1, `${where}: an unsent chat message in front holds the reload`);
    await toBackground(page);
    await reloaded;
    assert.equal(state.loads, 2, `${where}: one reload`);
    await card(page, where, "Your unsent message is still here.", `${name}-chat-restored`);
    assert.equal(await composer.inputValue(), "Half a thought", `${where}: the composer has the message back`);
  },
  async field(page, state, where, name) {
    const field = page.getByRole("textbox", { name: "Skill name" });
    await field.fill("Weekly digest");
    const reloaded = update(page, state, "b");
    await card(page, where, "This window reloads once unsaved text is saved or cleared.", `${name}-field-held`);
    await toBackground(page);
    await page.waitForTimeout(2500); // two more checks: the background lifts no hold on text nothing saves
    assert.equal(state.loads, 1, `${where}: text nothing saves holds the reload in the background too`);
    assert.equal(await field.inputValue(), "Weekly digest", `${where}: the text is still there`);
    await field.fill("");
    await reloaded;
    assert.equal(state.loads, 2, `${where}: one reload once cleared`);
  },
  async flap(page, state, where, name) {
    await update(page, state, "b"); // reload 1, onto b
    await update(page, state, "a"); // hz fell back: reload 2, onto a
    await page.getByRole("textbox", { name: "Your message" }).waitFor();
    state.build = "b";
    await backOnline(page);
    await card(page, where, /runs an older Heinzel version/, `${name}-flap-stuck`);
    await page.waitForTimeout(2500);
    assert.equal(state.loads, 3, `${where}: two reloads, then it offers Reload instead of looping`);
  },
};

try {
  for (const [name, engine] of [
    ["chromium", chromium],
    ["webkit", webkit],
  ]) {
    const browser = await engine.launch();
    try {
      for (const [scenario, run] of Object.entries(scenarios)) {
        const where = `${name} ${scenario}`;
        const state = { build: "a", loads: 0 };
        const { context, page } = await open(browser, state);
        await page.waitForTimeout(500); // the first check finds the page current: nothing happens
        assert.equal(state.loads, 1, `${where}: no reload while the page runs the served build`);
        await run(page, state, where, name);
        assert.deepEqual(state.errors, [], `${where}: page errors`);
        assert.deepEqual(state.unknown, [], `${where}: requests outside the fixture`);
        results.push({ browser: name, scenario, loads: state.loads });
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
