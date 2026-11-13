// The About dialog in Chromium and WebKit, on a phone (375px, touch) and a desktop window: every fact of
// `hz system about` shows (core, web build, desktop app, install source, channel, daemon, each plugin and its pin),
// the dialog fits the viewport, passes axe, and Copy diagnostics copies the report's lines. All API calls are answered
// locally. The page and the module it mounts live here, served by a Vite plugin, so this driver is the one file its
// journey owns. Optional argument: a directory for one screenshot per browser and viewport.
import assert from "node:assert/strict";
import { mkdirSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { chromium, webkit } from "playwright-core";
import { createServer } from "vite";

const require = createRequire(import.meta.url);
const axe = readFileSync(require.resolve("axe-core/axe.min.js"), "utf8");
const [shots] = process.argv.slice(2);
if (shots) mkdirSync(shots, { recursive: true });

const plugins = [
  { name: "heinzel-weather", version: "1.4.0", source: "PyPI", pinned: null, installed: "2026-11-01" },
  { name: "heinzel-calendar", version: "0.9.0", source: "git", pinned: "v0.9.0", installed: "2026-10-20" },
  {
    name: "heinzel-a-plugin-with-a-really-long-distribution-name",
    version: "0.1.0",
    source: "path",
    pinned: "local folder",
    installed: null,
  },
];
const about = {
  version: "0.9.42",
  installed: "2026-11-12",
  installation: "uv tool",
  source: "PyPI",
  channel: "stable",
  web_build: "0123456789ab",
  desktop: "0.9.42",
  daemon: { state: "running", version: "0.9.42", outdated: false },
  plugins,
  python_version: "3.14.1",
  platform: "macOS-27.0-arm64",
  diagnostics: [
    "hz 0.9.42 (installed 2026-11-12)",
    "install uv tool, from PyPI, channel stable",
    "web build 0123456789ab",
    "desktop app 0.9.42",
    "daemon running hz 0.9.42",
    "python 3.14.1",
    "platform macOS-27.0-arm64",
    "plugin heinzel-weather 1.4.0 from PyPI, follows updates",
    "plugin heinzel-calendar 0.9.0 from git, pinned to v0.9.0",
  ],
};
const answers = {
  "/api/system/about": about,
  "/api/system/info": {
    version: "0.9.42",
    latest_release: "0.9.42",
    plugins: plugins.map(({ name }) => ({
      name,
      repository:
        name === "heinzel-calendar" ? "https://github.com/helmut-hoffer-von-ankershoffen/heinzel-calendar" : null,
    })),
  },
};
const VIEWPORTS = [
  { width: 375, height: 760, isMobile: true, hasTouch: true },
  { width: 1100, height: 800, isMobile: false, hasTouch: false },
];
// What the dialog must say, by row label.
const FACTS = [
  ["Version", "hz 0.9.42"],
  ["Web build", "Development"], // the fixture page is served by Vite: no hashed build of its own
  ["Desktop app", "0.9.42, installed on this Mac"],
  ["Installed with", "uv tool from PyPI"],
  ["Channel", "stable"],
  ["Daemon", "Running, hz 0.9.42"],
];

// The real About dialog, mounted once as Shell mounts it, and a button that asks for it as the footer version does.
const PAGE = `<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>About dialog fixture</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/@about-dialog/page.tsx"></script>
  </body>
</html>`;
const MODULE = `
import { createRoot } from "react-dom/client";
import { Providers, queryClient } from "@/App";
import { AboutDialog } from "@/about/AboutDialog";
import { requestMenuAction } from "@/about/menu";
import "@/index.css";

sessionStorage.setItem("hz.token", "fixture-session");
createRoot(document.getElementById("root")).render(
  <Providers client={queryClient()}>
    <main className="min-h-screen bg-background p-3 text-foreground">
      <button type="button" className="min-h-11 rounded border px-3" onClick={() => requestMenuAction("about")}>
        Open About
      </button>
    </main>
    <AboutDialog />
  </Providers>,
);
`;
const fixture = {
  name: "about-dialog-fixture",
  resolveId: (id) => (id === "/@about-dialog/page.tsx" ? id : null),
  load: (id) => (id === "/@about-dialog/page.tsx" ? MODULE : null),
  configureServer(vite) {
    vite.middlewares.use("/e2e/about-dialog.html", async (request, response) => {
      response.setHeader("Content-Type", "text/html");
      response.end(await vite.transformIndexHtml(request.originalUrl ?? "/e2e/about-dialog.html", PAGE));
    });
  },
};

const server = await createServer({ server: { host: "127.0.0.1", port: 0 }, plugins: [fixture] });
await server.listen();
const address = server.httpServer.address();
assert(address && typeof address !== "string");
const results = [];
try {
  for (const [name, engine] of [
    ["chromium", chromium],
    ["webkit", webkit],
  ]) {
    const browser = await engine.launch();
    try {
      for (const viewport of VIEWPORTS) {
        const { width, height, isMobile, hasTouch } = viewport;
        const page = await browser.newPage({ viewport: { width, height }, isMobile, hasTouch });
        const where = `${name} ${width}px`;
        const unknown = [];
        const pageErrors = [];
        page.on("pageerror", (error) => pageErrors.push(String(error)));
        // Headless WebKit grants no clipboard: record what the page writes instead.
        await page.addInitScript(() => {
          window.copied = [];
          Object.defineProperty(navigator, "clipboard", {
            configurable: true,
            value: { writeText: async (text) => void window.copied.push(text) },
          });
        });
        await page.route(
          (url) => url.pathname.startsWith("/api/"),
          (route) => {
            const path = new URL(route.request().url()).pathname;
            if (path in answers) return route.fulfill({ json: answers[path] });
            unknown.push(path);
            return route.fulfill({ status: 404, json: { error: "not in this fixture" } });
          },
        );
        await page.goto(`http://127.0.0.1:${address.port}/e2e/about-dialog.html`);
        const opener = page.getByRole("button", { name: "Open About" });
        await (hasTouch ? opener.tap() : opener.click());
        const dialog = page.getByRole("dialog", { name: "Heinzelmännchen" });
        await dialog.getByText("Installed with", { exact: true }).waitFor();
        for (const [label, value] of FACTS) {
          const row = dialog.locator("div", { has: page.locator("dt", { hasText: label }) }).last();
          const text = (await row.locator("dd").textContent()) ?? "";
          assert.ok(text.startsWith(value), `${where}: ${label} says ${JSON.stringify(text)}`);
        }
        const rows = dialog.getByRole("row");
        assert.equal(await rows.count(), plugins.length + 1, `${where}: one row per plugin`);
        for (const text of ["Follows updates", "Pinned to v0.9.0", "Pinned to local folder", "first-party"])
          await dialog.getByText(text, { exact: true }).waitFor();
        const fits = await dialog.evaluate((element) => {
          const panel = element.firstElementChild.getBoundingClientRect();
          const { clientWidth, clientHeight } = document.documentElement;
          return (
            panel.left >= 0 &&
            panel.right <= clientWidth &&
            panel.top >= 0 &&
            panel.bottom <= clientHeight &&
            document.documentElement.scrollWidth <= window.innerWidth
          );
        });
        assert.ok(fits, `${where}: the dialog fits the viewport without horizontal overflow`);
        const small = await dialog.evaluate(
          (element) =>
            [...element.querySelectorAll("[data-pane-action]")]
              .map((button) => button.getBoundingClientRect())
              .filter((rect) => rect.width < 44 || rect.height < 44).length,
        );
        if (isMobile) assert.equal(small, 0, `${where}: every action at least 44px on a phone`);
        await page.evaluate(axe);
        const report = await dialog.evaluate((element) =>
          window.axe.run(element, {
            runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"] },
          }),
        );
        assert.deepEqual(
          report.violations.map((item) => item.id),
          [],
          `${where}: accessibility`,
        );
        if (shots) await page.screenshot({ path: `${shots}/about-${name}-${width}.png` });
        const copy = dialog.getByRole("button", { name: "Copy diagnostics" });
        await (hasTouch ? copy.tap() : copy.click());
        await dialog.getByRole("button", { name: "Copied" }).waitFor();
        const [copied] = await page.evaluate(() => window.copied);
        assert.deepEqual(
          copied.split("\n"),
          [...about.diagnostics, "web build of this page development"],
          `${where}: Copy diagnostics copies hz system about's lines and this page's build`,
        );
        await page.keyboard.press("Escape");
        await dialog.waitFor({ state: "detached" });
        assert.deepEqual(pageErrors, [], `${where}: page errors`);
        assert.deepEqual(unknown, [], `${where}: requests outside the fixture`);
        results.push({ browser: name, width });
        await page.close();
      }
    } finally {
      await browser.close();
    }
  }
  console.log(JSON.stringify(results));
} finally {
  await server.close();
}
