// Real footer panes in emulated phone browsers (Chromium and WebKit, 375px): every pane is content, then the frame's
// divider and one row of icon+text or icon-only buttons; no action or underlined text in the content (design/README.md,
// "Actions"). All API calls are answered locally. Optional argument: a directory for one screenshot per pane.
import assert from "node:assert/strict";
import { mkdirSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { chromium, webkit } from "playwright-core";
import { createServer } from "vite";

const require = createRequire(import.meta.url);
const axe = readFileSync(require.resolve("axe-core/axe.min.js"), "utf8");
const [shots] = process.argv.slice(2);
if (shots) mkdirSync(shots, { recursive: true });

const call = (operation) => ({
  method: "POST",
  path: `/fixture/${operation}`,
  query: {},
  body: {},
  command: `hz fixture ${operation}`,
  summary: `${operation} sharing`,
  read_only: false,
});
const entry = { module: "fixture", plugin: "heinzel-fixture", order: 500, refresh: 60, problem: null };
const answers = {
  "/api/heinzel/sessions": {
    sessions: [
      { session_id: "hz-01ARZ3NDEKTSV4RRFFQ69G5FAV", name: "Morning check", state: "awake", last_seq: 4 },
      { session_id: "hz-01ARZ3NDEKTSV4RRFFQ69G5FAW", name: "Quiet helper", state: "paused", last_seq: 2 },
    ],
  },
  "/api/system/doctor": {
    status: "degraded",
    checks: {
      "system.doctor": [{ name: "python", status: "up", detail: "3.14.0", fix: "" }],
      org: [{ name: "profile", status: "degraded", detail: "missing org.handle", fix: "Run `hz setup`" }],
    },
  },
  "/api/system/ui/status": [
    {
      ...entry,
      id: "fixture:weather",
      slots: { footer: true },
      item: {
        text: "12° Light rain",
        icon: "cloud-rain",
        tone: "neutral",
        detail: [
          { text: "Light rain · Woodtown", tone: "neutral" },
          { text: "Feels like 10°", tone: "neutral" },
        ],
        effect: { kind: "navigate", path: "/doctor" },
      },
    },
    {
      ...entry,
      id: "fixture:share",
      order: 510,
      slots: { footer: true },
      item: {
        text: "Sharing",
        icon: "link",
        tone: "ok",
        detail: [{ text: "Reachable from your other devices.", tone: "neutral" }],
        actions: [{ label: "Check sharing", icon: "list-checks", effect: { kind: "navigate", path: "/doctor" } }],
        toggle: {
          label: "Share over the private network",
          checked: true,
          requested: true,
          url: "https://workstation.example.test/",
          enable: { operation: "enable", arguments: {}, call: call("enable") },
          disable: { operation: "disable", arguments: {}, call: call("disable") },
          apply_settings: {},
          status: null,
          problem: null,
        },
      },
    },
  ],
};
// Trigger name, pane name, and the actions the row must keep, in order (same actions, same targets, just moved).
const PANES = [
  ["2 Heinzelmännchen", "Heinzelmännchen", ["Open chat"]],
  ["System health", "System health", ["Open Doctor", "Refresh health checks"]],
  ["12° Light rain", "12° Light rain", ["Open"]],
  // Share where the browser can share a link, else Copy link.
  ["Sharing", "Sharing", ["Share|Copy link", "Open", "Check sharing"]],
];

const server = await createServer({ server: { host: "127.0.0.1", port: 0 } });
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
      const page = await browser.newPage({ viewport: { width: 375, height: 760 }, isMobile: true, hasTouch: true });
      const unknown = [];
      const pageErrors = [];
      page.on("pageerror", (error) => pageErrors.push(String(error)));
      await page.route(
        (url) => url.pathname.startsWith("/api/"),
        (route) => {
          const path = new URL(route.request().url()).pathname;
          if (path in answers) return route.fulfill({ json: answers[path] });
          unknown.push(path);
          return route.fulfill({ status: 404, json: { error: "not in this fixture" } });
        },
      );
      await page.goto(`http://127.0.0.1:${address.port}/e2e/footer-panes.html`);
      await page.getByRole("contentinfo").waitFor();
      const shares = await page.evaluate(() => typeof navigator.share === "function");
      for (const [trigger, pane, actions] of PANES) {
        const button = page.getByRole("button", { name: new RegExp(`^${trigger}`) });
        await button.tap();
        const region = page.getByRole("region", { name: pane, exact: true });
        await region.waitFor();
        await page.evaluate(
          () => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))),
        );
        const facts = await region.evaluate((section) => {
          const rows = [...section.querySelectorAll("[data-pane-actions]")];
          const row = rows[0];
          const outside = [...section.querySelectorAll("button, a[href], [role=button], [role=link]")]
            .filter((element) => !row?.contains(element))
            .filter((element) => !["switch", "radio"].includes(element.getAttribute("role") ?? ""))
            .map((element) => element.getAttribute("aria-label") ?? element.textContent?.trim());
          const underlined = [...section.querySelectorAll("*")]
            .filter((element) => getComputedStyle(element).textDecorationLine.includes("underline"))
            .map((element) => element.textContent?.trim());
          const buttons = row ? [...row.querySelectorAll("[data-pane-action]")] : [];
          const box = section.getBoundingClientRect();
          return {
            rows: rows.length,
            frame: row?.hasAttribute("data-pane-frame") ?? false,
            last: section.lastElementChild === row,
            divider: row ? getComputedStyle(row).borderTopWidth : null,
            outside,
            underlined,
            names: buttons.map((element) => element.getAttribute("aria-label") ?? element.textContent?.trim()),
            lead: buttons[0]?.getAttribute("data-pane-action") === "text" || row?.getAttribute("data-words") === "none",
            // A wrapped row's lead sits alone on a line above every icon.
            above:
              row?.getAttribute("data-words") !== "wrap" ||
              buttons
                .slice(1)
                .every((element) => element.getBoundingClientRect().top > buttons[0].getBoundingClientRect().top),
            tops: new Set(
              (row?.getAttribute("data-words") === "wrap" ? buttons.slice(1) : buttons).map((element) =>
                Math.round(element.getBoundingClientRect().top),
              ),
            ).size,
            small: buttons
              .map((element) => element.getBoundingClientRect())
              .filter((rect) => rect.width < 44 || rect.height < 44).length,
            inside: box.left >= 0 && box.right <= document.documentElement.clientWidth,
          };
        });
        const where = `${name} ${pane}`;
        assert.equal(facts.rows, 1, `${where}: one action row`);
        assert.ok(facts.frame && facts.last, `${where}: the row is the frame's, after all content`);
        assert.equal(facts.divider, "1px", `${where}: a divider above the row`);
        assert.deepEqual(facts.outside, [], `${where}: no action in the content`);
        assert.deepEqual(facts.underlined, [], `${where}: no underlined text`);
        const expected = actions.map((action) =>
          action === "Share|Copy link" ? (shares ? "Share" : "Copy link") : action,
        );
        assert.deepEqual(facts.names, expected, `${where}: the same actions`);
        assert.ok(facts.lead, `${where}: the lead shows its words`);
        assert.equal(facts.tops, 1, `${where}: one line`);
        assert.ok(facts.above, `${where}: the wrapped lead sits on its own line above the icons`);
        assert.equal(facts.small, 0, `${where}: every target at least 44px`);
        assert.ok(facts.inside, `${where}: inside the viewport`);
        await page.evaluate(axe);
        const report = await region.evaluate((section) =>
          window.axe.run(section, {
            runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"] },
          }),
        );
        assert.deepEqual(
          report.violations.map((item) => item.id),
          [],
          `${where}: accessibility`,
        );
        if (shots) {
          const file = pane.toLowerCase().replace(/[^a-z0-9]+/g, "-");
          await page.screenshot({ path: `${shots}/${name}-375-${file}.png` });
        }
        await page.keyboard.press("Escape");
        await page.mouse.click(10, 60);
        await region.waitFor({ state: "detached" });
      }
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);
      assert.ok(overflow, `${name}: no horizontal document overflow`);
      assert.deepEqual(pageErrors, [], `${name}: page errors`);
      assert.deepEqual(unknown, [], `${name}: requests outside the fixture`);
      results.push({ browser: name, width: 375, panes: PANES.length });
    } finally {
      await browser.close();
    }
  }
  console.log(JSON.stringify(results));
} finally {
  await server.close();
}
