// Phone viewports must never pan sideways: neither the document nor a vertical scroller may scroll horizontally.
// Mobile/touch emulation in Chromium; not physical iPhone Safari.
// Usage: node e2e/no-horizontal-pan.mjs URL SCREENSHOT_DIR
import assert from "node:assert/strict";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { chromium } from "playwright-core";

const [url, shots] = process.argv.slice(2);
mkdirSync(shots, { recursive: true });
const viewports = [
  { name: "iphone-375", width: 375, height: 812 },
  { name: "iphone-390", width: 390, height: 844 },
];
const wideBundle = `/plugin-assets/specimen/forecast/${"b".repeat(64)}/index.js`;

// A contributed page panel shaped like a forecast: a header with icon actions, a wide grid and a long word.
async function contributeForecast(page) {
  await page.route("**/api/system/ui/views**", (route) =>
    route.fulfill({
      json: [
        {
          id: "specimen:forecast",
          module: "specimen",
          plugin: "heinzel-unforeseen",
          title: "Forecast",
          fallback: "Forecast is unavailable",
          slot: "panel",
          order: 500,
          refresh: 60,
          abi: 1,
          section: null,
          route: null,
          bundle: wideBundle,
          styles: [],
          actions: {},
          source: null,
          problem: null,
        },
      ],
    }),
  );
  await page.route(`**${wideBundle}`, (route) =>
    route.fulfill({
      contentType: "text/javascript",
      body: `
    export default sdk => function Forecast({onClose}) {
      const e=sdk.React.createElement;
      return e("section",{"aria-label":"Forecast"},
        e("header",{style:{display:"flex",justifyContent:"space-between",padding:12}},
          e("h2",null,"FORECAST"),e(sdk.Button,{onClick:onClose,"aria-label":"Close Forecast"},"Close")),
        e("p",null,"Times are shown in the forecast location's time zone, which may differ from your time zone."),
        e("div",{style:{display:"grid",gridTemplateColumns:"repeat(2,minmax(9rem,1fr))",gap:16,padding:16}},
          ...["Wind","Humidity","Chance of rain","Rain, next 24 h"].map(label=>e("div",{key:label},label))),
        e("div",{style:{overflowX:"auto"}},e("table",null,e("tbody",null,e("tr",null,
          ...Array.from({length:24},(_,h)=>e("td",{key:h,style:{padding:"0 12px"}},String(h).padStart(2,"0")+":00")))))));
    }`,
    }),
  );
}

async function ready(page) {
  await page.locator("#splash").waitFor({ state: "hidden" });
  await page.waitForFunction(() => !document.getElementById("root")?.hasAttribute("inert"));
  await page.waitForTimeout(400);
}

// Facts after trying to pan every vertical scroller and the page sideways, as a touch pan would.
function panFacts() {
  const intended = (element) =>
    element.closest(
      "pre, table, .xterm, .hz-ticker, [role=tablist], [class*='overflow-x-auto'], [class*='overflow-auto'], [style*='overflow-x']",
    );
  const panned = [];
  const scrollers = [document.scrollingElement, ...document.querySelectorAll("body *")].filter((element) => {
    if (!element || element.closest("[hidden], [aria-hidden=true]")) return false;
    const style = getComputedStyle(element);
    // Horizontal scrollers, and vertical ones: their x axis can be hidden, yet still scrolled by focus or script.
    return element === document.scrollingElement || /(auto|scroll)/.test(style.overflowX + style.overflowY);
  });
  for (const element of scrollers) {
    if (element !== document.scrollingElement && intended(element)) continue;
    const edge = element.getBoundingClientRect().right + 1;
    // Named before the pan moves them: the children that make this scroller wider than itself.
    const wide = [...element.querySelectorAll("*")]
      .filter((child) => !intended(child.parentElement) && child.getBoundingClientRect().right > edge)
      .slice(0, 8)
      .map((child) => `${child.tagName.toLowerCase()}.${String(child.className).slice(0, 90)}`);
    element.scrollLeft = 10_000;
    if (element.scrollLeft > 0)
      panned.push({
        element: `${element.tagName.toLowerCase()}.${String(element.className).slice(0, 90)}`,
        scrollLeft: element.scrollLeft,
        scrollWidth: element.scrollWidth,
        clientWidth: element.clientWidth,
        wide,
      });
    element.scrollLeft = 0;
  }
  // Elements whose box reaches past the right viewport edge, unless an overflow-x container inside the viewport
  // scrolls or clips them.
  const contained = (element) => {
    for (let parent = element.parentElement; parent && parent !== document.body; parent = parent.parentElement)
      if (getComputedStyle(parent).overflowX !== "visible" && parent.getBoundingClientRect().right <= innerWidth + 1)
        return true;
    return false;
  };
  const beyond = [];
  for (const element of document.querySelectorAll("#root *")) {
    if (element.closest("[hidden], [aria-hidden=true], .sr-only")) continue;
    const box = element.getBoundingClientRect();
    if (box.width > 0 && box.height > 0 && box.right > innerWidth + 1 && !contained(element))
      beyond.push(`${element.tagName.toLowerCase()}.${String(element.className).slice(0, 90)} right=${box.right}`);
  }
  return {
    width: innerWidth,
    scrollWidth: document.documentElement.scrollWidth,
    bodyScrollWidth: document.body.scrollWidth,
    panned,
    beyond: beyond.slice(0, 12),
  };
}

const browser = await chromium.launch().catch(() => chromium.launch({ channel: "chrome" }));
const cases = [];
try {
  for (const viewport of viewports) {
    const context = await browser.newContext({
      viewport: { width: viewport.width, height: viewport.height },
      isMobile: true,
      hasTouch: true,
      deviceScaleFactor: 3,
      serviceWorkers: "block",
    });
    const page = await context.newPage();
    await contributeForecast(page);
    const visit = async (name, path, open) => {
      const target = new URL(url);
      const [pathname, search = ""] = path.split("?");
      target.pathname = pathname;
      target.search = search;
      await page.goto(target.href);
      await ready(page);
      if (open) await open();
      const facts = await page.evaluate(panFacts);
      cases.push({ viewport: viewport.name, page: name, ...facts });
      await page.screenshot({ path: join(shots, `no-pan-${viewport.name}-${name}.png`) });
      console.error(`NO_PAN:${JSON.stringify(cases.at(-1))}`);
    };
    await visit("today", "/");
    await visit("forecast", "/?panel=specimen:forecast", () =>
      page.getByRole("heading", { name: "FORECAST" }).waitFor(),
    );
    await visit("notifications", "/?panel=notifications");
    await visit("calendar", "/", async () => {
      await page.getByRole("button", { name: "Calendar" }).first().click();
      await page.waitForTimeout(400);
    });
    await visit("heinzels", "/heinzel");
    await visit("settings", "/?settings=hz", () => page.getByRole("searchbox", { name: "Filter settings" }).waitFor());
    await visit("terminal", "/", async () => {
      await page.getByRole("button", { name: "Terminal" }).first().click();
      await page.waitForTimeout(400);
    });
    await context.close();
  }
} finally {
  await browser.close();
}
writeFileSync(join(shots, "no-horizontal-pan.json"), `${JSON.stringify(cases, null, 2)}\n`);
for (const facts of cases) {
  const where = `${facts.viewport} ${facts.page}`;
  assert.ok(facts.scrollWidth <= facts.width, `${where}: the page is ${facts.scrollWidth}px wide in ${facts.width}px`);
  assert.deepEqual(facts.panned, [], `${where}: a vertical scroller pans sideways`);
  assert.deepEqual(facts.beyond, [], `${where}: content reaches past the right edge`);
}
console.log(JSON.stringify({ cases: cases.map(({ viewport, page }) => ({ viewport, page })) }));
