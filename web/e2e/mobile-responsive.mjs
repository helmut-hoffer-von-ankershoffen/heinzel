// Focused mobile/touch viewport proof for the app served by `hz ui` (tests/e2e/web_ui_test.py).
// These are Chromium/WebKit emulations, not physical iPhone Safari.
// Usage: node e2e/mobile-responsive.mjs URL SCREENSHOT_DIR [SECTION]. SECTION is one viewport name or "phone" (the
// scroll, sound and notification flows), or "share-chromium" / "share-webkit"; without it every viewport and phone flow runs.
import assert from "node:assert/strict";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { chromium, webkit } from "playwright-core";
import ts from "typescript";
import { contributePanel } from "./contributed-panel.mjs";
import { fetchOnce, ONE_CONNECTION } from "./fetch-once.mjs";
import { reportMediaFailure, watchMedia } from "./media-diagnostics.mjs";

// Fixed, credential-free checkpoints for timeout diagnosis; do not interpolate page or URL data.
function stage(name) {
  console.error(`MOBILE_STAGE:${name}`);
  console.error(`MOBILE_ELAPSED:${Math.round(performance.now() / 1000)}`);
}
stage("launch");
const [url, shots] = process.argv.slice(2);
mkdirSync(shots, { recursive: true });
const section = process.argv[4] ?? "all";
const useWebKit =
  section === "share-webkit" || (section !== "share-chromium" && process.env.PLAYWRIGHT_BROWSER === "webkit");
const browser = useWebKit
  ? await webkit.launch()
  : await chromium.launch().catch(() => chromium.launch({ channel: "chrome" }));
stage("launched");
const cases = [
  { name: "narrow-portrait", width: 360, height: 780 },
  { name: "wide-portrait", width: 430, height: 932 },
  { name: "short-portrait", width: 390, height: 667 },
  { name: "narrow-landscape", width: 844, height: 390 },
  { name: "wide-landscape", width: 932, height: 430 },
  { name: "short-landscape", width: 844, height: 320 },
];
assert.ok(
  section === "all" ||
    section === "phone" ||
    ["share-chromium", "share-webkit"].includes(section) ||
    cases.some((spec) => spec.name === section),
  `Unknown mobile section "${section}"`,
);
// A viewport section keeps just its own case; "phone" keeps none, and runs the phone flows instead.
if (section !== "all") cases.splice(0, cases.length, ...cases.filter((spec) => spec.name === section));
const runPhone = section === "all" || section === "phone";
// Discover route literals from the real router so new screens join the mobile sweep automatically.
const routeSource = ts.createSourceFile(
  "App.tsx",
  readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8"),
  ts.ScriptTarget.Latest,
  true,
  ts.ScriptKind.TSX,
);
const routePaths = new Set(["/"]);
function discoverRoutes(node) {
  if (
    ts.isPropertyAssignment(node) &&
    node.name.getText(routeSource) === "path" &&
    ts.isStringLiteral(node.initializer)
  ) {
    const path = node.initializer.text;
    if (!path.includes(":") && path !== "*" && path !== "settings" && path !== "notifications")
      routePaths.add(`/${path}`);
  }
  ts.forEachChild(node, discoverRoutes);
}
discoverRoutes(routeSource);
const result = {
  engine: `${useWebKit ? "WebKit" : "Chromium"} ${browser.version()}`,
  evidence: "mobile/touch emulation; not physical iPhone Safari",
  cases: [],
};

async function viewportFacts(page, name) {
  const facts = await page.evaluate(() => {
    const root = document.documentElement;
    const shell = document.querySelector('[data-testid="app-shell"]');
    const safe = document.createElement("div");
    safe.style.cssText = "position:fixed;padding-bottom:env(safe-area-inset-bottom);height:100dvh;visibility:hidden";
    document.body.append(safe);
    const safeAreaBottom = getComputedStyle(safe).paddingBottom;
    const dynamicHeight = safe.getBoundingClientRect().height;
    safe.remove();
    const main = document.querySelector("main");
    const conversation = document.querySelector('[aria-label="Chat"] .overflow-y-auto');
    return {
      width: innerWidth,
      height: innerHeight,
      documentWidth: root.scrollWidth,
      bodyWidth: document.body.scrollWidth,
      shell: shell?.getBoundingClientRect().toJSON(),
      shellClientHeight: shell?.clientHeight,
      safeAreaEnvSupported: CSS.supports("padding-bottom: env(safe-area-inset-bottom)"),
      safeAreaBottom,
      dynamicViewportUnitSupported: CSS.supports("height: 100dvh"),
      dynamicViewportHeight: dynamicHeight,
      visualViewport: window.visualViewport
        ? {
            width: window.visualViewport.width,
            height: window.visualViewport.height,
            scale: window.visualViewport.scale,
          }
        : null,
      scrollOwners: {
        main: main ? getComputedStyle(main).overflowY : null,
        workspace: getComputedStyle(document.querySelector(".workspace-scroll")).overflowY,
        conversation: conversation ? getComputedStyle(conversation).overflowY : null,
      },
    };
  });
  assert.ok(
    facts.documentWidth <= facts.width + 1,
    `${name}: document has horizontal overflow ${facts.documentWidth} > ${facts.width}`,
  );
  assert.ok(
    facts.bodyWidth <= facts.width + 1,
    `${name}: body has horizontal overflow ${facts.bodyWidth} > ${facts.width}`,
  );
  assert.ok(facts.dynamicViewportUnitSupported, `${name}: Chromium does not support dvh`);
  assert.ok(
    Math.abs(facts.dynamicViewportHeight - facts.height) <= 1,
    `${name}: 100dvh does not match viewport height`,
  );
  assert.ok(
    facts.scrollOwners.main === "auto" || facts.scrollOwners.workspace === "auto",
    `${name}: main content must be in a vertical scroll area`,
  );
  assert.equal(facts.scrollOwners.conversation, "auto", `${name}: chat conversation must own vertical scrolling`);
  return facts;
}

async function assertTouchTarget(locator, description, name) {
  const box = await locator.boundingBox();
  assert.ok(
    box && box.width >= 24 && box.height >= 24,
    `${name}: ${description} is too small for touch: ${JSON.stringify(box)}`,
  );
  return box;
}

async function assertContained(page, locator, name) {
  await locator.waitFor();
  await locator.scrollIntoViewIfNeeded();
  await page.waitForFunction(
    () => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))),
  );
  await page.waitForFunction(
    (element) => {
      const box = element.getBoundingClientRect(),
        viewport = visualViewport;
      return (
        box.left >= (viewport?.offsetLeft ?? 0) - 1 &&
        box.top >= (viewport?.offsetTop ?? 0) - 1 &&
        box.right <= (viewport?.offsetLeft ?? 0) + (viewport?.width ?? innerWidth) + 1 &&
        box.bottom <= (viewport?.offsetTop ?? 0) + (viewport?.height ?? innerHeight) + 1
      );
    },
    await locator.elementHandle(),
    { timeout: 5000 },
  );
  // DOM and visualViewport share layout coordinates; Playwright boundingBox can normalize pinch-pan offsets.
  const box = await locator.evaluate((element) => element.getBoundingClientRect().toJSON());
  const visible = await page.evaluate(() => ({
    left: visualViewport?.offsetLeft ?? 0,
    top: visualViewport?.offsetTop ?? 0,
    width: visualViewport?.width ?? innerWidth,
    height: visualViewport?.height ?? innerHeight,
  }));
  assert.ok(
    box &&
      box.x >= visible.left - 1 &&
      box.y >= visible.top - 1 &&
      box.x + box.width <= visible.left + visible.width + 1 &&
      box.y + box.height <= visible.top + visible.height + 1,
    `${name}: outside visible viewport ${JSON.stringify({ box, visible })}`,
  );
  return box;
}

async function dismissFooterPane(page) {
  // A footer pane can cover arbitrary viewport corners, and its accessible region name is content-dependent.
  // Focus its own expanded trigger so Escape reaches the component's close handler without a coordinate click.
  const footer = page.getByRole("contentinfo");
  const controls = footer.locator("[aria-controls]");
  for (let attempt = 0; attempt < 8; attempt++) {
    let trigger;
    let pane;
    for (let index = 0; index < (await controls.count()); index++) {
      const candidate = controls.nth(index);
      const paneId = await candidate.getAttribute("aria-controls");
      if (!paneId) continue;
      const candidatePane = page.locator(`[id="${paneId}"]`);
      if (await candidatePane.isVisible()) {
        trigger = candidate;
        pane = candidatePane;
        break;
      }
    }
    if (!trigger || !pane) return;
    await trigger.focus();
    await page.keyboard.press("Escape");
    await pane.waitFor({ state: "hidden", timeout: 5000 });
  }
  assert.fail("Escape did not collapse every visible footer pane");
}

async function showPage(page) {
  // Touch viewports can retain a hover target after a layout change. Move the mouse clear of the footer before
  // observing its panes; then wait for the safe-area/layout update to settle before sending a real touch tap.
  await page.mouse.move(3, 3);
  await dismissFooterPane(page);
  const tab = page.getByRole("tab", { name: "Page", exact: true });
  if (!(await tab.isVisible())) return;
  await page.evaluate(() => {
    window.__mobilePageTabRect = undefined;
  });
  await page.waitForFunction(() => {
    const tabs = [...document.querySelectorAll('[role="tab"]')];
    const pageTab = tabs.find(
      (element) => element.getAttribute("aria-label") === "Page" || element.textContent?.trim() === "Page",
    );
    const footer = document.querySelector("footer");
    const panes = [...document.querySelectorAll("footer [aria-controls]")]
      .map((trigger) => document.getElementById(trigger.getAttribute("aria-controls") ?? ""))
      .filter((pane) => pane && pane.getClientRects().length > 0);
    if (!pageTab || !footer || footer.matches(":hover") || panes.length > 0) return false;
    const rect = pageTab.getBoundingClientRect();
    const current = `${rect.x}:${rect.y}:${rect.width}:${rect.height}`;
    const previous = window.__mobilePageTabRect;
    window.__mobilePageTabRect = current;
    return previous === current;
  });
  await tab.tap();
}

async function footerPane(page, id, label, ready) {
  if (ready) {
    await ready;
    await page.waitForFunction(
      () => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve(true)))),
    );
  }
  const trigger = page.locator(`[data-footer-content="${id}"] button`).first();
  // A missing status item is still loading, not necessarily in More. Its hidden measurement host and More's
  // retained button are not actionable. Wait for either the item or its actual overflow path to be visible.
  const direct = trigger.filter({ visible: true });
  const more = page
    .locator(
      `footer:has([data-footer-part="${id}"][data-footer-hidden="overflow"]) [data-footer-content="@more"] button[aria-expanded="false"]`,
    )
    .filter({ visible: true });
  // Resolve the currently actionable path as part of the real touch action. A snapshot of direct visibility
  // can become obsolete when an independent provider changes footer capacity before Playwright sends the tap.
  await direct.or(more).first().tap();
  if ((await trigger.getAttribute("aria-expanded")) !== "true") await direct.tap();
  const pane = page.getByRole("region", { name: label, exact: true });
  await pane.waitFor();
  await assertOneActionRow(pane, `${id} pane`);
  return pane.locator("..");
}

/**
 * A pane's actions (design/README.md, "Actions"): one row that never wraps, the first with its words (or the row's
 * declared `data-words="none"` fallback), the rest named icons, and every target at least 44px on this touch screen.
 */
async function assertOneActionRow(pane, name) {
  const facts = await pane.evaluate((element) => {
    const row = element.querySelector("[data-pane-actions]");
    const actions = row ? [...row.querySelectorAll("[data-pane-action]")] : [];
    return {
      words: row?.getAttribute("data-words") ?? null,
      actions: actions.map((action) => {
        const box = action.getBoundingClientRect();
        return {
          top: Math.round(box.top),
          width: box.width,
          height: box.height,
          kind: action.getAttribute("data-pane-action"),
          text: action.textContent.trim(),
          label: action.getAttribute("aria-label"),
        };
      }),
    };
  });
  if (!facts.actions.length) return;
  assert.equal(new Set(facts.actions.map((action) => action.top)).size, 1, `${name}: its actions wrap`);
  const [lead, ...rest] = facts.actions;
  if (facts.words === "lead") assert.ok(lead.kind === "text" && lead.text, `${name}: the lead shows its words`);
  else if (facts.words === "none") assert.ok(lead.kind === "icon" && lead.label, `${name}: the lead's fallback icon`);
  if (facts.words !== "all")
    for (const action of rest)
      assert.ok(action.kind === "icon" && action.label, `${name}: ${action.text} is a named icon`);
  for (const action of facts.actions)
    assert.ok(action.height >= 44 && action.width >= 44, `${name}: ${action.label ?? action.text} is under 44px`);
}

try {
  if (section === "share-chromium" || section === "share-webkit") {
    stage("phone-share");
    // A synthetic status exercises the real footer component without a plugin or a private URL.
    for (const supported of [true, false]) {
      const page = await browser.newPage({
        viewport: { width: 360, height: 780 },
        isMobile: true,
        hasTouch: true,
        serviceWorkers: "block",
      });
      await page.addInitScript((supported) => {
        localStorage.setItem("hz.onboarding", JSON.stringify({ step: "you", visited: [], closed: true }));
        window.shareProbe = { calls: 0, copied: null };
        Object.defineProperty(navigator, "share", {
          configurable: true,
          value: supported
            ? async (data) => {
                window.shareProbe.calls++;
                window.shareProbe.url = data.url;
                if (window.shareProbe.calls === 1) throw new DOMException("Cancelled", "AbortError");
                throw new Error("private detail");
              }
            : undefined,
        });
        Object.defineProperty(navigator, "clipboard", {
          configurable: true,
          value: {
            writeText: async (text) => {
              window.shareProbe.copied = text;
            },
          },
        });
      }, supported);
      const fixtureUrl = "https://workstation.tail-example.ts.net";
      const entry = {
        id: "probe:share",
        module: "probe",
        plugin: null,
        order: 1,
        refresh: 60,
        slots: { footer: true },
        problem: null,
        item: {
          text: "Share probe",
          tone: "ok",
          icon: "link",
          detail: [],
          toggle: {
            label: "Share this service",
            checked: true,
            url: fixtureUrl,
            sign_in_url: null,
            enable: { call: { method: "POST", path: "/probe/enable", read_only: false, query: {}, body: {} } },
            disable: { call: { method: "POST", path: "/probe/disable", read_only: false, query: {}, body: {} } },
          },
        },
      };
      const statusRequested = Promise.withResolvers();
      const publishStatus = Promise.withResolvers();
      await page.route("**/api/system/ui/status**", async (route) => {
        statusRequested.resolve();
        await publishStatus.promise;
        const selected = new URL(route.request().url()).searchParams.get("item");
        if (selected === entry.id) return route.fulfill({ json: [entry] });
        const response = await fetchOnce(route);
        const entries = await response.json();
        return route.fulfill({ response, json: [...entries, ...(!selected ? [entry] : [])] });
      });
      await page.goto(url);
      await page.locator("#splash").waitFor({ state: "hidden", timeout: 15_000 });
      await statusRequested.promise;
      // Other real contributions can already require More. A held, unpublished item must have neither its
      // trigger nor an overflow entry; shell readiness alone cannot make that item actionable.
      const held = await page.evaluate(() => ({
        target: !!document.querySelector('[data-footer-content="probe:share"] button'),
        overflow: !!document.querySelector('[data-footer-part="probe:share"][data-footer-hidden="overflow"]'),
      }));
      assert.deepEqual(held, { target: false, overflow: false });
      const opening = footerPane(page, entry.id, "Share probe");
      publishStatus.resolve();
      const pane = await opening;
      const copy = pane.getByRole("button", { name: "Copy URL", exact: true });
      await assertContained(page, pane, "phone: share pane");
      await page.screenshot({ path: join(shots, `mobile-${section}-${supported ? "supported" : "unsupported"}.png`) });
      if (supported) {
        const share = pane.getByRole("button", { name: "Share", exact: true });
        await assertContained(page, share, "phone: Share");
        await assertTouchTarget(share, "Share", "phone");
        assert.equal(await copy.count(), 0, "Native sharing replaces the separate Copy button");
        await share.tap();
        assert.equal(await page.evaluate(() => window.shareProbe.calls), 1);
        assert.equal(await pane.getByRole("alert").count(), 0, "Cancellation is silent");
        await copy.waitFor(); // AbortError can also mean that no share targets were available.
        await share.tap();
        await pane.getByRole("alert").getByText("Could not share the link. You can copy it instead.").waitFor();
        assert.equal(await page.evaluate(() => window.shareProbe.url), `${fixtureUrl}/`);
      } else {
        assert.equal(await pane.getByRole("button", { name: "Share", exact: true }).count(), 0);
      }
      await assertContained(page, copy, "phone: Copy URL");
      await assertTouchTarget(copy, "Copy URL", "phone");
      if (supported) await page.screenshot({ path: join(shots, `mobile-${section}-failed.png`) });
      await copy.tap();
      await page.waitForFunction((expected) => window.shareProbe.copied === expected, `${fixtureUrl}/`);
      assert.equal(
        await pane.evaluate((element) => element.scrollWidth <= element.clientWidth + 1),
        true,
        "Share controls and URL must wrap within the pane",
      );
      await page.close();
    }
    result.share = { cancellation: true, failure: true, copy: true, unsupported: true, wraps: true };
  }
  for (const spec of cases) {
    stage(`viewport-${spec.name}-start`);
    const context = await browser.newContext({
      viewport: { width: spec.width, height: spec.height },
      screen: { width: spec.width, height: spec.height },
      isMobile: true,
      // Layout probes intercept provider labels; a controlling worker can bypass Playwright routes.
      serviceWorkers: "block",
      hasTouch: true,
      deviceScaleFactor: 2,
      isLandscape: spec.width > spec.height,
      colorScheme: spec.width >= 390 ? "dark" : "light",
    });
    const page = await context.newPage();
    await contributePanel(page);
    // Platform-specific CLI paths and provider names must wrap rather than widen their settings pane.
    await page.route("**/api/ai/access/list", async (route) => {
      const response = await fetchOnce(route);
      const listing = await response.json();
      listing.options.push({
        name: "mobile-overflow-probe",
        title: "AnExtremelyLongProviderNameThatMustWrapOnAPhone",
        summary: "A provider with an installation command.",
        module: "ai.access",
        plugin: null,
        in_use: false,
        status: "needs setup",
        needs: "Add the AnExtremelyLongProviderNameThatMustWrapOnAPhone client",
        setup: "client",
        detail: "",
        fix: "uv tool install an-extremely-long-provider-distribution-name-for-mobile-layout",
        models: [],
        runner: null,
        hosts: [],
        last_test: null,
      });
      await route.fulfill({ response, json: listing });
    });
    // Include advanced controls using the real isolated-profile operation.
    const expertResponse = await context.request.post(new URL("/api/system/expert/on", url).href, {
      headers: { ...ONE_CONNECTION, Authorization: `Bearer ${new URL(url).hash.slice("#token=".length)}` },
    });
    assert.ok(expertResponse.ok(), "Advanced settings must be enabled in the isolated fixture");
    // Enum display labels come from providers and can exceed a native select's intrinsic phone width.
    await page.route("**/api/system/config/show", async (route) => {
      const response = await fetchOnce(route);
      const report = await response.json();
      const backend = report.settings.find(
        (setting) => setting.section === "system.credential" && setting.key === "backend",
      );
      assert.ok(backend, "The mobile sweep must exercise the real credential backend selector");
      backend.json_schema["x-labels"] = {
        ...backend.json_schema["x-labels"],
        auto: "Automatic: the system keychain if there is one — with a provider-specific explanatory label",
      };
      await route.fulfill({ response, json: report });
    });
    let updateAvailable = false;
    let updateRequests = 0;
    // Plain or streamed: no viewport check may start an update.
    await page.route(
      (address) => ["/api/system/update", "/api/stream/system/update"].includes(address.pathname),
      (route) => {
        updateRequests += 1;
        return route.abort();
      },
    );
    await page.route("**/api/update-status", (route) =>
      route.fulfill({
        json: {
          current: "0.7.30",
          latest: updateAvailable ? "0.7.31" : "0.7.30",
          installed_at: "2026-01-02T12:00:00Z",
          available: updateAvailable,
          supported: true,
          checked_at: new Date().toISOString(),
          last_ok_at: new Date().toISOString(),
          check_failed: false,
          reason: null,
        },
      }),
    );
    // The automatic-update plan is pinned too: this test server runs from a source checkout, which holds updates.
    await page.route("**/api/auto-update", (route) =>
      route.request().method() === "GET"
        ? route.fulfill({
            json: {
              enabled: true,
              reason: null,
              phase: "idle",
              version: "0.7.30",
              core: null,
              plugins: {},
              seconds_left: null,
              apply_at: null,
              next_window_at: null,
              postponements: 0,
              message: null,
              holds: [],
              checked_at: new Date().toISOString(),
              core_checked: true,
              plugins_checked: true,
            },
          })
        : route.continue(),
    );
    await page.goto(url);
    await page.getByRole("heading", { name: "Today" }).waitFor();
    await page.locator("#splash").waitFor({ state: "hidden", timeout: 15_000 });
    await page.waitForFunction(() => !document.getElementById("root")?.hasAttribute("inert"), null, { timeout: 5000 });
    stage(`viewport-${spec.name}-loaded`);
    const facts = await viewportFacts(page, spec.name);
    if (facts.shellClientHeight !== undefined) {
      assert.ok(facts.shellClientHeight <= facts.height, `${spec.name}: shell exceeds the viewport height`);
    }

    // Long phone pages scroll under the header and footer, which stay visible and usable.
    const scrollSelector = await page.evaluate(() =>
      getComputedStyle(document.querySelector(".workspace-scroll")).overflowY === "auto" ? ".workspace-scroll" : "main",
    );
    const scroller = page.locator(scrollSelector);
    const topbar = page.locator(".workspace-topbar");
    const footer = page.locator("footer").last();
    await page.locator("main").evaluate((main) => {
      const probe = document.createElement("div");
      probe.id = "mobile-scroll-probe";
      probe.style.height = "1500px";
      main.append(probe);
    });
    const beforeScroll = { top: await topbar.boundingBox(), bottom: await footer.boundingBox() };
    const moved = await scroller.evaluate((element) => {
      element.scrollTop = 600;
      return element.scrollTop;
    });
    assert.ok(moved > 0, `${spec.name}: page did not scroll`);
    const afterScroll = { top: await topbar.boundingBox(), bottom: await footer.boundingBox() };
    assert.ok(beforeScroll.top && beforeScroll.bottom && afterScroll.top && afterScroll.bottom);
    assert.ok(await topbar.isVisible(), `${spec.name}: header is hidden while scrolling`);
    assert.ok(await footer.isVisible(), `${spec.name}: footer is hidden while scrolling`);
    assert.ok(Math.abs(afterScroll.top.y - beforeScroll.top.y) <= 1, `${spec.name}: header moved while scrolling`);
    assert.ok(
      Math.abs(afterScroll.bottom.y - beforeScroll.bottom.y) <= 1,
      `${spec.name}: footer moved while scrolling`,
    );
    assert.ok(afterScroll.top.y >= 0 && afterScroll.bottom.y + afterScroll.bottom.height <= spec.height + 1);
    await page.getByRole("button", { name: "Today", exact: true }).click();
    assert.ok(await page.getByRole("menu").isVisible(), `${spec.name}: header menu is unreachable`);
    await page.keyboard.press("Escape");
    await page.screenshot({ path: join(shots, `mobile-${spec.name}-sticky-scroll.png`) });
    await page.locator("#mobile-scroll-probe").evaluate((element) => element.remove());
    await scroller.evaluate((element) => {
      element.scrollTop = 0;
    });

    // Main navigation remains touch reachable in the collapsed rail.
    const navigation = page.getByRole("navigation", { name: "Main" });
    const navLink = navigation.getByRole("link", { name: "Heinzelmännchen" });
    const navTarget = await assertTouchTarget(navLink, "navigation link", spec.name);
    await navLink.click();
    await page.getByRole("heading", { name: "Heinzelmännchen", exact: true }).waitFor();
    await page.screenshot({ path: join(shots, `mobile-${spec.name}-navigation.png`) });

    // Exercise every everyday navigation screen at this phone size, beyond just the Home shell.
    const destinations = await navigation
      .getByRole("link")
      .evaluateAll((links) => links.map((link) => link.getAttribute("href")));
    const visitedRoutes = new Set([...destinations, ...routePaths]);
    stage(`viewport-${spec.name}-routes`);
    for (const destination of visitedRoutes) {
      await page.goto(`${new URL(destination, url).href}${new URL(url).hash}`);
      await page.locator("#splash").waitFor({ state: "hidden", timeout: 15_000 });
      await page.locator("main").waitFor();
      for (const detail of await page
        .locator('main a[href^="/contracts/"]')
        .evaluateAll((links) => links.map((link) => link.getAttribute("href"))))
        visitedRoutes.add(detail);
      const horizontal = await page.evaluate(() => ({
        width: innerWidth,
        content: document.documentElement.scrollWidth,
      }));
      assert.ok(horizontal.content <= horizontal.width + 1, `${spec.name}: ${destination} overflows horizontally`);
    }
    // The account menu is a portal/popover anchored at the bottom edge on phones; keep its full box visible.
    const accountButton = page.getByRole("button", { name: /Mira/ });
    await assertTouchTarget(accountButton, "account menu button", spec.name);
    await accountButton.click();
    const menu = page.getByRole("menu");
    await menu.waitFor();
    const menuBox = await menu.boundingBox();
    assert.ok(menuBox, `${spec.name}: account menu has no box`);
    assert.ok(
      menuBox.x >= 0 && menuBox.y >= 0,
      `${spec.name}: account menu starts outside viewport: ${JSON.stringify(menuBox)}`,
    );
    assert.ok(
      menuBox.x + menuBox.width <= spec.width + 1,
      `${spec.name}: account menu exceeds viewport width: ${JSON.stringify(menuBox)}`,
    );
    assert.ok(
      menuBox.y + menuBox.height <= spec.height + 1,
      `${spec.name}: account menu exceeds viewport height: ${JSON.stringify(menuBox)}`,
    );
    await page.screenshot({ path: join(shots, `mobile-${spec.name}-account-menu.png`) });
    const settingsItem = menu.getByRole("menuitem", { name: "Settings" });
    const settingsTarget = await assertTouchTarget(settingsItem, "Settings menu item", spec.name);
    await settingsItem.click();
    await page.getByRole("dialog", { name: "Settings", exact: true }).waitFor();
    const settingsDialog = page.getByRole("dialog", { name: "Settings", exact: true });
    await assertContained(page, settingsDialog, `${spec.name}: settings`);
    await assertContained(
      page,
      settingsDialog.getByRole("button", { name: "Close settings" }),
      `${spec.name}: settings close`,
    );
    const searchField = page.getByRole("searchbox", { name: "Filter settings" });
    const settingsFont = await searchField.evaluate((element) => ({
      fontSize: getComputedStyle(element).fontSize,
      parentFontSize: getComputedStyle(element.parentElement).fontSize,
      coarse: matchMedia("(pointer: coarse)").matches,
      anyCoarse: matchMedia("(any-pointer: coarse)").matches,
      hover: matchMedia("(hover: hover)").matches,
      width: innerWidth,
      touchPoints: navigator.maxTouchPoints,
    }));
    writeFileSync(join(shots, "settings-font.json"), `${JSON.stringify(settingsFont, null, 2)}\n`);
    console.error(`MOBILE_SETTINGS_FONT:${JSON.stringify({ viewport: spec.name, ...settingsFont })}`);
    await page.screenshot({ path: join(shots, `mobile-${spec.name}-settings-font.png`) });
    assert.ok(
      Number.parseFloat(settingsFont.fontSize) >= 16,
      `${spec.name}: focused phone form text must not trigger Safari zoom: ${JSON.stringify(settingsFont)}`,
    );
    await page.screenshot({ path: join(shots, `mobile-${spec.name}-settings.png`) });
    const settingsPages = await settingsDialog
      .getByLabel("Settings page", { exact: true })
      .locator("option")
      .evaluateAll((options) => options.map((option) => ({ id: option.value, title: option.textContent.trim() })));
    assert.ok(settingsPages.length > 5, `${spec.name}: the complete settings registry must be exercised`);
    stage(`viewport-${spec.name}-settings`);
    for (const settingsPage of settingsPages) {
      if (spec.width < 640)
        await settingsDialog.getByLabel("Settings page", { exact: true }).selectOption(settingsPage.id);
      else
        await settingsDialog
          .getByRole("navigation", { name: "Settings", exact: true })
          .getByRole("button", { name: settingsPage.title, exact: true })
          .click();
      await assertContained(page, settingsDialog, `${spec.name}: settings ${settingsPage.id}`);
      if (settingsPage.id === "web-server") {
        const restart = settingsDialog.getByRole("button", { name: "Restart web server", exact: true });
        await assertTouchTarget(restart, "Restart web server", spec.name);
        await restart.click();
        const confirmation = settingsDialog.getByRole("alertdialog", { name: "Restart web server", exact: true });
        await assertContained(page, confirmation, `${spec.name}: web server restart confirmation`);
        assert.match(await confirmation.textContent(), /This page reconnects for you/);
        await page.screenshot({ path: join(shots, `mobile-${spec.name}-web-server-confirmation.png`) });
        await confirmation.getByRole("button", { name: "Cancel", exact: true }).click();
        await confirmation.waitFor({ state: "hidden" });
      }
      if (settingsPage.id === "credentials") {
        await settingsDialog
          .locator("option")
          .filter({
            hasText: "Automatic: the system keychain if there is one — with a provider-specific explanatory label",
          })
          .waitFor({ state: "attached" });
      }
      const content = settingsDialog.locator("[data-settings-content]");
      await content.evaluate((element) => {
        element.scrollTop = element.scrollHeight;
      });
      const sizing = await content.evaluate((element) => ({
        width: element.clientWidth,
        content: element.scrollWidth,
      }));
      assert.ok(
        sizing.content <= sizing.width + 1,
        `${spec.name}: settings ${settingsPage.id} clips horizontally ${JSON.stringify(sizing)}`,
      );
      await page.screenshot({
        path: join(shots, `mobile-${spec.name}-settings-${settingsPage.id.replaceAll(/[^a-z0-9-]/gi, "-")}.png`),
      });
    }

    // A phone closes the dialog with a touch. A mouse click would leave Playwright's pointer hovering the top-right
    // corner, where the Audio rail's trigger sits once the dialog is gone, and open its hover preview over the footer.
    await page.getByRole("button", { name: "Close settings" }).tap();

    // Every footer pane must fit, including the wide release notes and controls moved into More.
    for (const [id, label] of [["@version", /^v/]]) {
      const pane = await footerPane(page, id, label);
      await assertContained(page, pane, `${spec.name}: ${id}`);
      await page.screenshot({ path: join(shots, `mobile-${spec.name}-${id.slice(1)}.png`) });
      await dismissFooterPane(page);
    }
    const updatePane = await footerPane(page, "@updates", "Updates");
    await assertContained(page, updatePane, `${spec.name}: update pane`);
    await updatePane.getByText(/^Heinzel 0\.7\.30 is up to date · checked /).waitFor();
    await updatePane.getByRole("button", { name: "Manually update…", exact: true }).waitFor();
    await updatePane.getByText(/^Last installed:/).waitFor();
    await page.screenshot({ path: join(shots, `mobile-${spec.name}-updates.png`) });
    await dismissFooterPane(page);
    updateAvailable = true;
    // The footer's label and its fit depend on this answer, so tap only once the footer shows it.
    const updateAnswered = page.waitForResponse("**/api/update-status");
    await page.goto(url);
    await page.getByRole("heading", { name: "Today" }).waitFor();
    await page.locator("#splash").waitFor({ state: "hidden", timeout: 15_000 });
    const availablePane = await footerPane(page, "@updates", "Updates available", updateAnswered);
    await assertContained(page, availablePane, `${spec.name}: available update pane`);
    await availablePane.getByText(/^Last installed:/).waitFor();
    await assertTouchTarget(availablePane.getByRole("button", { name: "Update now" }), "update action", spec.name);
    assert.equal(updateRequests, 0, `${spec.name}: inspecting the update must not start installation`);
    await page.screenshot({ path: join(shots, `mobile-${spec.name}-update-available.png`) });
    await dismissFooterPane(page);
    updateAvailable = false;
    await page.goto(url);
    await page.getByRole("heading", { name: "Today" }).waitFor();
    await page.locator("#splash").waitFor({ state: "hidden", timeout: 15_000 });
    await page.getByRole("button", { name: /^Notifications/ }).click();
    const notifications = page.getByRole("region", { name: "Notifications", exact: true });
    await assertContained(page, notifications, `${spec.name}: notifications`);
    await page.getByRole("button", { name: "Close notifications" }).click();
    await page.getByRole("button", { name: "Search", exact: true }).click();
    const search = page.getByRole("dialog", { name: /^Search navigation/ });
    await assertContained(page, search, `${spec.name}: search`);
    const searchInput = search.getByRole("combobox");
    await searchInput.fill("unmatched-search-probe");
    await search.getByText("Nothing matches.").waitFor();
    const clearSearch = search.getByRole("button", { name: "Clear search" });
    await assertTouchTarget(clearSearch, "clear search button", spec.name);
    await clearSearch.tap();
    assert.equal(await searchInput.inputValue(), "", `${spec.name}: search cleared`);
    assert.ok(await searchInput.evaluate((element) => element === document.activeElement));
    await clearSearch.waitFor({ state: "hidden" });
    await page.keyboard.press("Escape");

    // The home chat opens automatically. Check the composer is inside the screen and can be focused by touch.
    await page.goto(url);
    await page.getByRole("heading", { name: "Today" }).waitFor();
    await page.locator("#splash").waitFor({ state: "hidden", timeout: 15_000 });
    // Both sides of the workspace: full navigation, top menu, chat switcher and terminal menus/full screen.
    await page.getByRole("button", { name: "Expand navigation" }).click();
    await assertContained(
      page,
      page.getByRole("navigation", { name: "Main" }),
      `${spec.name}: expanded left navigation`,
    );
    await page.getByRole("button", { name: "Collapse navigation" }).click();
    await page.getByRole("button", { name: "Today", exact: true }).click();
    await assertContained(page, page.getByRole("menu"), `${spec.name}: screen menu`);
    await page.keyboard.press("Escape");
    const initialChatTab = page.getByRole("tab", { name: "Chat", exact: true });
    if (await initialChatTab.isVisible()) await initialChatTab.click();
    await page.getByRole("button", { name: /switch chats$/ }).click();
    await assertContained(
      page,
      page.getByRole("dialog", { name: "Your chats", exact: true }),
      `${spec.name}: chat switcher`,
    );
    await page.keyboard.press("Escape");
    await page.getByRole("textbox", { name: "Your message" }).fill("A draft to keep.");
    await assertContained(
      page,
      page.getByRole("button", { name: "Create a Heinzel and take this message" }),
      `${spec.name}: workshop agent handoff`,
    );
    await page.screenshot({ path: join(shots, `mobile-${spec.name}-agent-handoff.png`) });
    await showPage(page);
    await page.getByRole("button", { name: "Open a terminal", exact: true }).click();
    const terminal = page.getByRole("region", { name: "Terminal", exact: true });
    await assertContained(page, terminal, `${spec.name}: terminal`);
    await terminal.evaluate((element) => {
      window.mobileTerminal = element;
    });
    await page.evaluate(() => {
      history.pushState({}, "", "?panel=specimen:inspector");
      dispatchEvent(new PopStateEvent("popstate"));
    });
    await assertContained(
      page,
      page.getByRole("region", { name: "Specimen", exact: true }).getByRole("button", { name: "Close Specimen" }),
      `${spec.name}: world contributed header with chat and terminal open`,
    );
    if ((await page.locator('[data-placement="below"]').count()) > 0) {
      const scrollArea = page.locator(".workspace-scroll");
      const headerBefore = await topbar.boundingBox();
      const footerBefore = await footer.boundingBox();
      await scrollArea.evaluate((element) => {
        element.scrollTop = element.scrollHeight;
      });
      const headerAfter = await topbar.boundingBox();
      const footerAfter = await footer.boundingBox();
      assert.ok(await topbar.isVisible(), `${spec.name}: header hidden with Specimen selected`);
      assert.ok(headerBefore && headerAfter && footerBefore && footerAfter);
      assert.ok(Math.abs(headerBefore.y - headerAfter.y) <= 1, `${spec.name}: header moved in Specimen`);
      assert.ok(Math.abs(footerBefore.y - footerAfter.y) <= 1, `${spec.name}: footer moved in Specimen`);
      const end = await page
        .locator('[data-placement="below"]')
        .evaluate((element) => element.getBoundingClientRect().bottom);
      assert.ok(end <= footerAfter.y + 1, `${spec.name}: panel bottom is covered by footer`);
    }
    await page.getByRole("tab", { name: "Chat", exact: true }).click();
    assert.ok(await topbar.isVisible(), `${spec.name}: header hidden with Chat selected`);
    const chatFooter = await footerPane(page, "@version", /^v/);
    await assertContained(page, chatFooter, `${spec.name}: footer in chat`);
    await dismissFooterPane(page);
    assert.equal(await page.getByRole("textbox", { name: "Your message" }).inputValue(), "A draft to keep.");
    await page
      .getByRole("tablist", { name: "Workspace panels" })
      .getByRole("tab", { name: "Terminal", exact: true })
      .click();
    assert.ok(
      await terminal.evaluate((element) => element === window.mobileTerminal),
      "switching panels must preserve the terminal instance",
    );
    await terminal.getByRole("button", { name: "Terminal options", exact: true }).click();
    const terminalMenu = page.getByRole("menu");
    await assertContained(page, terminalMenu, `${spec.name}: terminal options`);
    await terminalMenu.getByRole("menuitem", { name: /^Cursor style/ }).click();
    await assertContained(page, terminalMenu, `${spec.name}: expanded terminal options`);
    await page.keyboard.press("Escape");
    await terminal.getByRole("button", { name: "Full screen", exact: true }).click();
    await assertContained(page, terminal, `${spec.name}: full-screen terminal`);
    await terminal.getByRole("button", { name: "Leave full screen", exact: true }).click();
    const terminalFooter = await footerPane(page, "@version", /^v/);
    await assertContained(page, terminalFooter, `${spec.name}: footer after full screen`);
    await dismissFooterPane(page);
    await terminal.getByRole("button", { name: "Close the terminal", exact: true }).click();
    await page.getByRole("button", { name: "Close the chat", exact: true }).first().click();
    await showPage(page);
    await page.getByRole("button", { name: "Chat with your Heinzelmännchen", exact: true }).click();
    const composer = page.getByRole("textbox", { name: "Your message" });
    await composer.waitFor();
    await composer.fill("");
    const composerBox = await composer.boundingBox();
    assert.ok(composerBox, `${spec.name}: composer has no box`);
    assert.ok(
      composerBox.x >= 0 && composerBox.y >= 0,
      `${spec.name}: composer starts outside viewport: ${JSON.stringify(composerBox)}`,
    );
    assert.ok(
      composerBox.x + composerBox.width <= spec.width + 1,
      `${spec.name}: composer exceeds viewport width: ${JSON.stringify(composerBox)}`,
    );
    assert.ok(
      composerBox.y + composerBox.height <= spec.height + 1,
      `${spec.name}: composer exceeds viewport height: ${JSON.stringify(composerBox)}`,
    );
    const modelPicker = page.getByRole("button", { name: /^Model and effort:/ });
    if (await modelPicker.isVisible()) {
      await modelPicker.click();
      const modelMenu = page.getByRole("group", { name: /^Models of/ }).locator("..");
      await assertContained(page, modelMenu, `${spec.name}: model and effort menu`);
      await page.keyboard.press("Escape");
    }
    await composer.tap();
    assert.equal(
      await composer.evaluate((element) => element === document.activeElement),
      true,
      `${spec.name}: composer did not receive touch focus`,
    );
    const visualViewportAfterFocus = await page.evaluate(() =>
      window.visualViewport
        ? {
            width: window.visualViewport.width,
            height: window.visualViewport.height,
            scale: window.visualViewport.scale,
          }
        : null,
    );
    assert.ok(visualViewportAfterFocus, `${spec.name}: visualViewport API is required for this emulation check`);
    // Pin a footer pane through the safe-area change so its open state is deterministic while the Page tab is next.
    if (spec.name === "short-landscape") {
      const versionTrigger = page.locator('[data-footer-content="@version"] button').first();
      if (!(await versionTrigger.isVisible())) await page.locator('[data-footer-content="@more"] button').click();
      await versionTrigger.tap();
      await page.getByRole("region", { name: /^v\d/ }).waitFor({ state: "visible" });
    }
    // Simulate the reduced visual viewport a software keyboard creates, and inject a representative iPhone safe
    // area. Chromium does not expose iOS Safari's keyboard or hardware insets, so this verifies our layout response.
    await page.evaluate(() => {
      const root = document.documentElement;
      root.style.setProperty("--hz-safe-area-top", "47px");
      root.style.setProperty("--hz-safe-area-right", "0px");
      root.style.setProperty("--hz-safe-area-bottom", "34px");
      root.style.setProperty("--hz-safe-area-left", "0px");
    });
    const safePadding = await page.getByTestId("app-shell").evaluate((element) => {
      const style = getComputedStyle(element);
      return { top: style.paddingTop, bottom: style.paddingBottom };
    });
    assert.equal(safePadding.top, "47px", `${spec.name}: shell must respect the top safe area`);
    assert.equal(safePadding.bottom, "34px", `${spec.name}: shell must respect the home-indicator safe area`);
    if (spec.name === "short-landscape")
      assert.ok(
        await page.getByRole("region", { name: /^v\d/ }).isVisible(),
        "pinned footer pane should remain open through the layout change",
      );
    await showPage(page);
    await page.getByRole("button", { name: /^Notifications/ }).click();
    const safeNotices = page.getByRole("region", { name: "Notifications", exact: true });
    const safeBox = await assertContained(page, safeNotices, `${spec.name}: safe-area notifications`);
    assert.ok(
      safeBox.y >= 47 && safeBox.y + safeBox.height <= spec.height - 34 + 1,
      `${spec.name}: notifications must respect device safe areas`,
    );
    await page.getByRole("button", { name: "Close notifications" }).click();
    await page.getByRole("tab", { name: "Chat", exact: true }).click();
    await composer.scrollIntoViewIfNeeded();
    await composer.tap();
    await page.setViewportSize({ width: spec.width, height: Math.max(240, spec.height - 180) });
    await page.waitForTimeout(100);
    const keyboardComposerBox = await composer.boundingBox();
    const reducedViewportHeight = await page.evaluate(() => window.visualViewport?.height ?? innerHeight);
    assert.ok(
      keyboardComposerBox && keyboardComposerBox.y + keyboardComposerBox.height <= reducedViewportHeight + 1,
      `${spec.name}: composer must remain visible after a keyboard-sized viewport reduction: ${JSON.stringify({ keyboardComposerBox, reducedViewportHeight })}`,
    );
    const keyboardViewport = await page.evaluate(() => ({
      visual: window.visualViewport?.height,
      layout: innerHeight,
      offsetTop: window.visualViewport?.offsetTop,
      shell: document.querySelector('[data-testid="app-shell"]')?.getBoundingClientRect().toJSON(),
    }));
    assert.equal(
      keyboardViewport.visual,
      keyboardViewport.layout,
      `${spec.name}: emulated visual viewport should track the resized layout viewport`,
    );
    assert.ok(
      keyboardComposerBox && keyboardComposerBox.y >= (keyboardViewport.offsetTop ?? 0),
      `${spec.name}: composer is above the visual viewport`,
    );
    await page.screenshot({ path: join(shots, `mobile-${spec.name}-chat.png`) });
    result.cases.push({
      ...spec,
      viewport: facts,
      settingsFont,
      accountMenu: menuBox,
      targets: { navigation: navTarget, settings: settingsTarget },
      composer: composerBox,
      navigationSettingsChat: true,
      routes: [...visitedRoutes],
      settingsPages: settingsPages.map((entry) => entry.id),
      visualViewportAfterFocus,
      safeAreaPadding: safePadding,
      keyboardReducedViewportComposer: keyboardComposerBox,
      keyboardViewport,
      touch: context.pages()[0].viewportSize(),
    });
    // Unlike setViewportSize, this keyboard probe shrinks only visualViewport, as Safari's keyboard does.
    await page.setViewportSize({ width: spec.width, height: spec.height });
    await page.evaluate(() => {
      window.originalVisualViewport = Object.getOwnPropertyDescriptor(window, "visualViewport");
      const viewport = Object.assign(new EventTarget(), {
        width: innerWidth,
        height: Math.max(240, innerHeight - 250),
        offsetLeft: 0,
        offsetTop: 0,
        scale: 1,
      });
      Object.defineProperty(window, "visualViewport", { configurable: true, value: viewport });
      window.dispatchEvent(new Event("resize"));
    });
    await assertContained(page, composer, `${spec.name}: visual-only keyboard composer`);
    await page.evaluate(() => {
      Object.defineProperty(window, "visualViewport", window.originalVisualViewport);
      visualViewport.dispatchEvent(new Event("resize"));
    });
    // A private fixture suggestion stays inert until the person selects it and saves the profile.
    await page.route("**/api/heinzel/latest-profile-proposal?*", async (route) => {
      const session = new URL(route.request().url()).searchParams.get("session_id");
      await route.fulfill({
        json: {
          session_id: session,
          request_id: "mobile-look",
          expected_revision: 1,
          name: "Mira",
          appearance: { age: "grown", build: "regular", shirt: "teal", accessories: [] },
          status: "proposed",
          preview_sha256: "a".repeat(64),
        },
      });
    });
    await page.getByRole("textbox", { name: "Your message" }).fill("Keep my notes current.");
    await page.getByRole("button", { name: "Create a Heinzel and take this message" }).click();
    await page.getByLabel("Message your Heinzel").waitFor();
    await page.getByText(/Customize.*suggested look/).click();
    const useSuggestion = page.getByRole("button", { name: "Use suggested identity" });
    await assertContained(page, useSuggestion, `${spec.name}: suggested identity review`);
    await useSuggestion.click();
    assert.equal(await page.getByLabel("Name", { exact: true }).inputValue(), "Mira");
    await page.screenshot({ path: join(shots, `mobile-${spec.name}-suggested-identity.png`) });
    // A real visual viewport zoom exercises fixed panels independently of layout viewport size.
    await page.goto(`${url.split("#")[0]}?settings=hz${new URL(url).hash}`);
    if (!useWebKit) {
      const session = await context.newCDPSession(page);
      await session.send("Emulation.setPageScaleFactor", { pageScaleFactor: 1.4 });
      assert.ok(await page.evaluate(() => visualViewport.scale > 1 && visualViewport.width < innerWidth));
      const zoomedSettings = page.getByRole("dialog", { name: "Settings", exact: true });
      await assertContained(page, zoomedSettings, `${spec.name}: zoomed settings`);
      await session.send("Emulation.setPageScaleFactor", { pageScaleFactor: 1 });
      await session.detach();
    }
    stage(`viewport-${spec.name}-cleanup`);
    await page.unrouteAll({ behavior: "wait" });
    await context.close();
    stage(`viewport-${spec.name}-done`);
  }
  if (runPhone) {
    stage("phone-scroll");
    // Header actions navigate within the shared phone scroller, without requiring a manual swipe.
    const scrollPage = await browser.newPage({
      viewport: { width: 390, height: 844 },
      isMobile: true,
      hasTouch: true,
      serviceWorkers: "block",
    });
    await scrollPage.addInitScript(() => {
      localStorage.setItem("hz.onboarding", JSON.stringify({ step: "you", visited: [], closed: true }));
      const scroll = Element.prototype.scrollTo;
      window.workspaceScrollCalls = [];
      window.workspaceTimers = { started: [], cleared: [] };
      const startTimer = window.setTimeout.bind(window);
      const clearTimer = window.clearTimeout.bind(window);
      window.setTimeout = (callback, delay, ...args) => {
        const id = startTimer(callback, delay, ...args);
        if (delay === 1200) window.workspaceTimers.started.push(id);
        return id;
      };
      window.clearTimeout = (id) => {
        window.workspaceTimers.cleared.push(id);
        return clearTimer(id);
      };
      Element.prototype.scrollTo = function (...args) {
        if (this.classList.contains("workspace-scroll")) window.workspaceScrollCalls.push(args[0]);
        return scroll.apply(this, args);
      };
    });
    await contributePanel(scrollPage, { delay: 350 });
    await scrollPage.goto(url);
    await scrollPage.getByRole("heading", { name: "Today" }).waitFor();
    await scrollPage.locator("#splash").waitFor({ state: "hidden", timeout: 15_000 });
    const workspaceScroll = scrollPage.locator(".workspace-scroll");
    const atPanel = async (id) => {
      await scrollPage.waitForFunction(
        (id) => {
          const box = document.querySelector(".workspace-scroll");
          const panel = document.querySelector('[data-placement="below"]');
          return (
            panel?.getAttribute("data-active") === id &&
            box &&
            Math.abs(box.scrollTop - Math.min(panel.offsetTop, box.scrollHeight - box.clientHeight)) < 2
          );
        },
        id,
        { timeout: 5000 },
      );
    };
    const atPage = async () => {
      await scrollPage.waitForFunction(
        () => {
          const box = document.querySelector(".workspace-scroll");
          return box && box.scrollTop < 2 && document.querySelector('[data-active="page"]');
        },
        null,
        { timeout: 5000 },
      );
    };
    const lastScroll = async () => scrollPage.evaluate(() => window.workspaceScrollCalls.at(-1));
    await scrollPage.getByRole("button", { name: "Specimen", exact: true }).tap();
    await scrollPage.getByRole("heading", { name: "Specimen", exact: true }).waitFor();
    await atPanel("specimen:inspector");
    assert.equal((await lastScroll()).behavior, "smooth", "contributed header scrolls smoothly");
    await workspaceScroll.evaluate((element) => {
      element.scrollTop = 0;
    });
    await scrollPage.getByRole("button", { name: "Specimen", exact: true }).tap();
    await atPanel("specimen:inspector");
    assert.equal((await lastScroll()).behavior, "smooth", "repeated panel selection scrolls again");
    const startTop = await workspaceScroll.evaluate((element) => element.scrollTop);
    await scrollPage.getByRole("tab", { name: "Specimen", exact: true }).focus();
    // Observe before Home: driver round trips may outlast the native animation.
    // Only actual owning-scroller events after one trusted Home can satisfy the
    // original intermediate-position predicate, within its original 5000ms budget.
    await workspaceScroll.evaluate((box, start) => {
      let settle;
      let timer;
      let deadline = 0;
      let done = false;
      const samples = [];
      const result = new Promise((resolve) => {
        settle = resolve;
      });
      const finish = (intermediate = false) => {
        if (done) return;
        done = true;
        window.removeEventListener("keydown", onHome, true);
        box.removeEventListener("scroll", onScroll);
        clearTimeout(timer);
        settle({ intermediate, samples });
      };
      const onScroll = (event) => {
        if (!event.isTrusted || !deadline || performance.now() > deadline) return;
        const top = box.scrollTop;
        if (samples.length < 32) samples.push(top);
        if (top > 2 && top < start - 2) finish(true);
      };
      const onHome = (event) => {
        if (!event.isTrusted || event.key !== "Home" || deadline) return;
        window.homeMotion.armed = true;
        deadline = performance.now() + 5000;
        window.removeEventListener("keydown", onHome, true);
        timer = setTimeout(() => finish(), 5000);
      };
      window.homeMotion = { result, armed: false, stop: finish };
      window.addEventListener("keydown", onHome, { capture: true, passive: true });
      box.addEventListener("scroll", onScroll, { passive: true });
    }, startTop);
    try {
      await scrollPage.keyboard.press("Home");
      assert.equal(
        await scrollPage.evaluate(() => window.homeMotion.armed),
        true,
        "the motion budget starts at trusted Home",
      );
      const pageTab = scrollPage.getByRole("tab", { name: "Page", exact: true });
      await pageTab.waitFor({ state: "visible" });
      await scrollPage.waitForFunction(
        () => document.querySelector('[role="tab"][aria-selected="true"]')?.textContent === "Page",
        null,
        { timeout: 1000 },
      );
      assert.ok(
        await pageTab.evaluate((element) => element === document.activeElement),
        "Page tab keeps keyboard focus",
      );
      const motion = await scrollPage.evaluate(() => window.homeMotion.result);
      assert.equal(motion.intermediate, true, "Home must produce a native intermediate scroll position within 5000ms");
      await atPage();
      assert.equal((await lastScroll()).behavior, "smooth", "Page scrolls smoothly to the top");
    } finally {
      await scrollPage.evaluate(() => window.homeMotion.stop());
    }
    await scrollPage.getByRole("button", { name: "Chat with your Heinzelmännchen" }).tap();
    await atPanel("chat");
    assert.equal((await lastScroll()).behavior, "smooth", "chat header scrolls smoothly");
    await workspaceScroll.evaluate((element) => {
      element.scrollTop = 0;
    });
    await scrollPage.getByRole("button", { name: "Show chat" }).tap();
    await atPanel("chat");
    await scrollPage.getByRole("tab", { name: "Page", exact: true }).tap();
    await atPage();
    await scrollPage.getByRole("button", { name: "Open a terminal" }).tap();
    await atPanel("terminal");
    assert.equal((await lastScroll()).behavior, "smooth", "terminal header scrolls smoothly");
    await workspaceScroll.evaluate((element) => {
      element.scrollTop = 0;
    });
    await scrollPage.getByRole("button", { name: "Show terminal" }).tap();
    await atPanel("terminal");
    // A later selection cancels the pending Page transition, even after its fallback timer.
    await scrollPage.evaluate(() => {
      window.workspaceTimers = { started: [], cleared: [] };
    });
    await scrollPage.getByRole("tab", { name: "Page", exact: true }).tap();
    await scrollPage.waitForFunction(() => window.workspaceTimers.started.length > 0);
    await scrollPage.getByRole("button", { name: "Chat with your Heinzelmännchen" }).tap();
    await atPanel("chat");
    const timers = await scrollPage.evaluate(() => window.workspaceTimers);
    assert.ok(
      timers.started.length > 0 && timers.cleared.includes(timers.started.at(-1)),
      "superseded Page timer is cancelled",
    );
    assert.equal(await scrollPage.locator('[data-placement="below"]').getAttribute("data-active"), "chat");
    await scrollPage.emulateMedia({ reducedMotion: "reduce" });
    await scrollPage.getByRole("tab", { name: "Page", exact: true }).tap();
    await atPage();
    assert.equal((await lastScroll()).behavior, "auto", "reduced motion avoids animation");
    await scrollPage.getByRole("button", { name: "Open a terminal" }).tap();
    await atPanel("terminal");
    assert.equal((await lastScroll()).behavior, "auto", "reduced motion also applies to panel navigation");
    await scrollPage.emulateMedia({ reducedMotion: "no-preference" });
    await scrollPage.evaluate(() => {
      window.workspaceTimers = { started: [], cleared: [] };
    });
    await scrollPage.getByRole("tab", { name: "Page", exact: true }).tap();
    await scrollPage.waitForFunction(() => window.workspaceTimers.started.length > 0);
    await scrollPage.getByRole("link", { name: "Heinzelmännchen", exact: true }).tap();
    await scrollPage.waitForURL("**/heinzel");
    await scrollPage.waitForFunction(() =>
      window.workspaceTimers.cleared.includes(window.workspaceTimers.started.at(-1)),
    );
    const routeTimers = await scrollPage.evaluate(() => window.workspaceTimers);
    assert.ok(routeTimers.cleared.includes(routeTimers.started.at(-1)), "route changes cancel the Page timer");
    await scrollPage.getByRole("button", { name: "Specimen", exact: true }).tap();
    await atPanel("specimen:inspector");
    await scrollPage.evaluate(() => {
      window.workspaceTimers = { started: [], cleared: [] };
    });
    await scrollPage.getByRole("tab", { name: "Page", exact: true }).tap();
    await scrollPage.waitForFunction(() => window.workspaceTimers.started.length > 0);
    await scrollPage.setViewportSize({ width: 1400, height: 900 });
    await scrollPage.waitForFunction(() => document.querySelector('[data-placement="beside"]'));
    await scrollPage.waitForFunction(() =>
      window.workspaceTimers.cleared.includes(window.workspaceTimers.started.at(-1)),
    );
    const resizeTimers = await scrollPage.evaluate(() => window.workspaceTimers);
    assert.ok(resizeTimers.cleared.includes(resizeTimers.started.at(-1)), "wide layout cancels the Page timer");
    await scrollPage.setViewportSize({ width: 390, height: 844 });
    await scrollPage.goto(url);
    await scrollPage.getByRole("heading", { name: "Today" }).waitFor();
    await scrollPage.locator("#splash").waitFor({ state: "hidden", timeout: 15_000 });
    await scrollPage.getByRole("button", { name: "Open a terminal" }).tap();
    await atPanel("terminal");
    const terminal = scrollPage.getByRole("region", { name: "Terminal", exact: true });
    const terminalKeys = terminal.getByRole("group", { name: "Terminal keys", exact: true });
    await assertContained(scrollPage, terminalKeys, "phone: terminal key row");
    for (const name of [
      "Escape",
      "Tab",
      "Control+C",
      "Control+Z",
      "Control+D",
      "Arrow left",
      "Arrow down",
      "Arrow up",
      "Arrow right",
    ]) {
      const key = terminalKeys.getByRole("button", { name, exact: true });
      await assertTouchTarget(key, `terminal ${name} key`, "phone");
    }
    const rightArrow = terminalKeys.getByRole("button", { name: "Arrow right", exact: true });
    await rightArrow.scrollIntoViewIfNeeded();
    await assertContained(scrollPage, rightArrow, "phone: horizontally scrolled terminal key");
    await scrollPage.screenshot({ path: join(shots, "mobile-terminal-keys.png") });
    const shellInput = terminal.locator(".xterm-helper-textarea");
    await scrollPage.waitForFunction(() => document.querySelector(".xterm-rows")?.textContent?.trim(), null, {
      timeout: 10_000,
    });
    await shellInput.pressSequentially("printf 'READY_%s\\n' ok; sleep 30");
    await shellInput.press("Enter");
    await scrollPage.waitForFunction(
      () => document.querySelector(".xterm-rows")?.textContent?.includes("READY_ok"),
      null,
      {
        timeout: 10_000,
      },
    );
    await terminalKeys.getByRole("button", { name: "Control+C", exact: true }).tap();
    await scrollPage.waitForFunction(() => document.querySelector(".xterm-rows")?.textContent?.includes("^C"), null, {
      timeout: 10_000,
    });
    await shellInput.pressSequentially("printf 'KEYS_TEST_%s\\n' ok");
    await shellInput.press("Enter");
    await scrollPage.waitForFunction(
      () => document.querySelector(".xterm-rows")?.textContent?.includes("KEYS_TEST_ok"),
      null,
      {
        timeout: 10_000,
      },
    );
    await scrollPage.setViewportSize({ width: 844, height: 320 });
    await scrollPage.getByRole("button", { name: "Show terminal" }).tap();
    await atPanel("terminal");
    // Bead f87: on hosted macOS this row stayed invisible for the whole wait, once in 13 main runs. The same wait now
    // says why: what hides the row (display, an inactive or hidden tab, a zero box) and which media rules apply.
    await terminalKeys.waitFor({ state: "visible" }).catch(async (error) => {
      const state = await scrollPage.evaluate(() => {
        const rows = [...document.querySelectorAll('fieldset[aria-label="Terminal keys"]')];
        return {
          viewport: [innerWidth, innerHeight],
          pointerFine: matchMedia("(pointer: fine)").matches,
          regions: document.querySelectorAll('section[aria-label="Terminal"], [role="region"][aria-label="Terminal"]')
            .length,
          rows: rows.map((row) => {
            const box = row.getBoundingClientRect();
            const view = row.parentElement;
            return {
              display: getComputedStyle(row).display,
              box: [Math.round(box.width), Math.round(box.height)],
              tabHidden: view?.getAttribute("aria-hidden") === "true",
              insideHidden: Boolean(row.closest("[hidden]")),
              tabBox: Math.round(view?.getBoundingClientRect().height ?? -1),
            };
          }),
        };
      });
      throw new Error(`${error.message}\nTerminal keys state: ${JSON.stringify(state)}`);
    });
    const landscapeEscape = terminalKeys.getByRole("button", { name: "Escape", exact: true });
    await landscapeEscape.scrollIntoViewIfNeeded();
    await assertTouchTarget(landscapeEscape, "landscape Escape key", "phone");
    await scrollPage.screenshot({ path: join(shots, "mobile-terminal-keys-landscape.png") });
    await scrollPage.getByRole("tab", { name: "Page", exact: true }).tap();
    await atPage();
    assert.equal((await lastScroll()).behavior, "smooth", "short landscape Page scrolls smoothly");
    await scrollPage.setViewportSize({ width: 390, height: 844 });
    await scrollPage.goto(`${new URL("/doctor?view=probe", url).href}${new URL(url).hash}`);
    await scrollPage.locator("main table").waitFor();
    await scrollPage.getByRole("button", { name: "Specimen", exact: true }).tap();
    await atPanel("specimen:inspector");
    assert.equal(new URL(scrollPage.url()).pathname, "/doctor", "panel keeps the current page");
    assert.equal(new URL(scrollPage.url()).searchParams.get("view"), "probe");
    await scrollPage.getByRole("tab", { name: "Page", exact: true }).tap();
    await atPage();
    assert.equal(new URL(scrollPage.url()).pathname, "/doctor", "Page returns to the original route");
    await scrollPage.screenshot({ path: join(shots, "mobile-workspace-navigation.png") });
    await scrollPage.unrouteAll({ behavior: "wait" });
    await scrollPage.close();

    // The full sidebar retains its dock slot at a phone-sized width. Check the player itself and its controls fit and
    // remain actionable after a touch gesture; this says nothing about physical iOS Safari behavior.
    const soundPage = await browser.newPage({
      userAgent:
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1",
      viewport: { width: 430, height: 932 },
      screen: { width: 430, height: 932 },
      isMobile: true,
      hasTouch: true,
      deviceScaleFactor: 2,
    });
    stage("phone-sound");
    await soundPage
      .context()
      .addCookies([
        { name: "hz_test", value: "signed", url: "https://www.youtube.com/", secure: true, sameSite: "None" },
      ]);
    await soundPage
      .context()
      .route("https://www.youtube.com/watch**", (route) =>
        route.fulfill({ contentType: "text/html", body: "<!doctype html><title>Watch fixture</title>" }),
      );
    await soundPage.route("https://www.youtube.com/embed/**", (route) =>
      route.fulfill({ contentType: "text/html", body: "<!doctype html><title>Account player fixture</title>" }),
    );
    const sound = {
      name: "mobile-dock-probe",
      title: "Mobile dock probe",
      kind: "stream",
      stream: {
        provider: "youtube",
        video: "tRsQsTMvPNg",
        frame_origin: "https://www.youtube-nocookie.com",
        embed_url: "https://www.youtube-nocookie.com/embed/tRsQsTMvPNg",
        watch_url: "https://www.youtube.com/watch?v=tRsQsTMvPNg",
      },
      module: null,
      plugin: null,
      selected: true,
      effective: true,
      fade_seconds: 0,
    };
    await soundPage.route("**/api/system/sound/list", (route) =>
      route.fulfill({
        json: {
          selected: sound.name,
          effective: sound.name,
          soundscapes: [sound],
          signals: {},
          volume: 50,
          follow_theme: false,
          mute_in_meetings: false,
          duck_while_typing: false,
        },
      }),
    );
    await soundPage.route("https://www.youtube-nocookie.com/embed/**", (route) =>
      route.fulfill({
        contentType: "text/html",
        body: `<!doctype html><html><body style="margin:0"><video muted autoplay playsinline></video><script>const c=document.createElement('canvas');c.width=320;c.height=180;const x=c.getContext('2d');setInterval(()=>{x.fillStyle='#308090';x.fillRect(0,0,320,180)},40);const v=document.querySelector('video');v.srcObject=c.captureStream(25);v.play();</script></body></html>`,
      }),
    );
    await watchMedia(soundPage);
    await soundPage.goto(url);
    await soundPage.getByRole("heading", { name: "Today" }).waitFor();
    await soundPage.locator("#splash").waitFor({ state: "hidden", timeout: 15_000 });
    await soundPage.getByRole("button", { name: "Expand navigation" }).click();
    await soundPage.getByRole("button", { name: "Collapse navigation" }).waitFor();
    // The expanding sidebar can move the sound button between mouse down and mouse up.
    await soundPage.waitForFunction(() => !document.getAnimations().some((run) => run instanceof CSSTransition));
    await soundPage.getByRole("button", { name: "Choose a soundscape", exact: true }).click();
    const soundDialog = soundPage.getByRole("dialog", { name: "Sound" });
    await soundDialog.getByText("Use your iPhone or iPad volume buttons to adjust YouTube audio.").waitFor();
    // Safari's toolbars and keyboard resize the window at the same width: the navigation and this dialog stay open.
    await soundPage.evaluate(() => window.dispatchEvent(new Event("resize")));
    await soundPage.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => setTimeout(resolve))));
    assert.equal(await soundDialog.count(), 1, "a same-width resize keeps the sound dialog open");
    assert.equal(await soundDialog.getByRole("slider", { name: "Volume" }).count(), 0);
    assert.equal(await soundDialog.getByRole("switch", { name: "Softer while I type" }).count(), 0);
    await assertContained(soundPage, soundDialog, "iPhone sound controls");
    await soundPage.screenshot({ path: join(shots, "mobile-iphone-sound-controls.png") });
    await soundPage
      .getByRole("dialog", { name: "Sound" })
      .getByRole("button", { name: "Play Mobile dock probe", exact: true })
      .click();
    await soundPage.keyboard.press("Escape");
    const player = soundPage.getByRole("region", { name: "Mobile dock probe player" });
    await player.waitFor();
    const privateFrame = await (await player.locator("iframe").elementHandle()).contentFrame();
    try {
      await privateFrame.waitForURL(/youtube-nocookie\.com\/embed\//);
    } catch (error) {
      await reportMediaFailure(soundPage, "mobile private frame");
      throw error;
    }
    assert.equal(await privateFrame.evaluate(() => document.cookie.includes("hz_test=signed")), false);
    const playerFrameBox = await player.locator("iframe").boundingBox();
    assert.ok(playerFrameBox.width >= 200 && playerFrameBox.height >= 200, "YouTube's minimum player size");
    assert.equal(
      await player.getAttribute("data-dock"),
      "docked",
      "wide-portrait: stream player should be docked in the sidebar",
    );
    const playerBox = await player.boundingBox();
    const popOut = soundPage.getByRole("button", { name: "Pop the player out of the sidebar" });
    const popOutBox = await assertTouchTarget(popOut, "docked player pop-out control", "wide-portrait");
    assert.ok(
      playerBox &&
        playerBox.x >= 0 &&
        playerBox.y >= 0 &&
        playerBox.x + playerBox.width <= 430 + 1 &&
        playerBox.y + playerBox.height <= 932 + 1,
      `wide-portrait: docked player must fit viewport: ${JSON.stringify(playerBox)}`,
    );
    await soundPage.getByRole("button", { name: "Collapse navigation" }).click();
    await soundPage.getByRole("button", { name: "Pause Mobile dock probe", exact: true }).tap();
    await soundPage.getByRole("button", { name: "Play Mobile dock probe", exact: true }).tap();
    assert.equal(await player.isVisible(), false, "collapsed dock must not cover the workspace");
    await soundPage.screenshot({ path: join(shots, "mobile-compact-sound.png") });
    await soundPage.getByRole("button", { name: "Expand navigation" }).click();
    await player.waitFor();
    // The player moves with the navigation's width transition: a tap during it can land beside the button.
    await soundPage.waitForFunction(() => !document.getAnimations().some((run) => run instanceof CSSTransition));
    await popOut.tap();
    await soundPage.waitForFunction(() => document.querySelector('[data-dock="floating"]'));
    await soundPage.setViewportSize({ width: 360, height: 400 });
    await assertContained(soundPage, player, "rotated floating player");
    const playbackHelp = player.getByRole("button", { name: /Playback or sign-in trouble\?/ });
    await assertTouchTarget(playbackHelp, "playback help button", "short-landscape");
    await playbackHelp.tap();
    await assertContained(soundPage, player, "expanded playback recovery");
    const accountChoice = player.getByRole("button", { name: "Use YouTube sign-in" });
    await assertTouchTarget(accountChoice, "YouTube account player choice", "short-landscape");
    await accountChoice.tap();
    assert.ok(
      (await player.locator("iframe").getAttribute("src")).startsWith("https://www.youtube.com/embed/"),
      "explicit sign-in choice must switch to the regular YouTube origin",
    );
    const accountContent = await (await player.locator("iframe").elementHandle()).contentFrame();
    await accountContent.waitForURL(/youtube\.com\/embed\//);
    if (!useWebKit) {
      assert.equal(
        await accountContent.evaluate(() => document.cookie.includes("hz_test=signed")),
        true,
        "the regular player can receive the browser's YouTube session when third-party cookies are allowed",
      );
    }
    await playbackHelp.tap();
    const accountFrame = await player.locator("iframe").elementHandle();
    await player.getByRole("button", { name: "Retry player" }).tap();
    assert.equal(await accountFrame.evaluate((frame) => frame.isConnected), false, "retry must replace the frame");
    await playbackHelp.tap();
    await player.getByRole("button", { name: "Use private player" }).tap();
    assert.ok(
      (await player.locator("iframe").getAttribute("src")).startsWith("https://www.youtube-nocookie.com/embed/"),
      "private player choice must restore the privacy-enhanced origin",
    );
    await playbackHelp.tap();
    const [youtubePage] = await Promise.all([
      soundPage.waitForEvent("popup"),
      player.getByRole("link", { name: "Open on YouTube" }).tap(),
    ]);
    await youtubePage.waitForURL("https://www.youtube.com/watch**");
    assert.equal(new URL(youtubePage.url()).searchParams.get("v"), sound.stream.video);
    await player.waitFor({ state: "detached" });
    await youtubePage.close();
    result.dockedPlayer = {
      viewport: { width: 430, height: 932 },
      dockedBox: playerBox,
      popOutTarget: popOutBox,
      popOutUsable: true,
    };
    stage("phone-notifications");
    // A phone tests this device, even when a Mac backend reports its own notification failures.
    await soundPage.setViewportSize({ width: 390, height: 844 });
    const collapseNavigation = soundPage.getByRole("button", { name: "Collapse navigation" });
    if (await collapseNavigation.isVisible()) await collapseNavigation.tap();
    const testNotice = {
      id: "20261028T190000Z-iphone",
      kind: "test",
      title: "Notifications work",
      body: "A test on this device.",
      route: "/notifications",
      command: "hz system notify list",
      key: "test:iphone",
      urgent: true,
      at: "2026-10-28T19:00:00Z",
      due: "2026-10-28T19:00:00Z",
      popup: true,
      held: null,
      delivered_by: null,
      delivered_at: null,
    };
    await soundPage.route("**/api/system/notify/list**", (route) =>
      route.fulfill({ json: { notices: [testNotice], waiting: 1, native: "through Heinzel Notifier on macOS" } }),
    );
    await soundPage.route("**/api/system/notify/test**", (route) => {
      assert.equal(new URL(route.request().url()).searchParams.get("native"), "false");
      return route.fulfill({
        status: 503,
        json: {
          notice: testNotice,
          native: "macOS doesn't allow Heinzel Notifier",
          channels: [
            { name: "web", state: "closed", detail: "no web app is open" },
            { name: "native", state: "blocked", detail: "macOS doesn't allow Heinzel Notifier", fix: "open-settings" },
          ],
          pops_up: false,
        },
      });
    });
    await soundPage.getByRole("button", { name: /^Notifications(,|$)/ }).tap();
    const notifications = soundPage.getByRole("region", { name: "Notifications", exact: true });
    await notifications.getByRole("button", { name: "Send a test notification" }).tap();
    const report = notifications.getByRole("region", { name: "How the test went" });
    await report.getByText("The test was added to Notifications in this app.").waitFor();
    await report.getByText(/Add to Home Screen/).waitFor();
    await notifications.getByText("Saved in Notifications").waitFor();
    assert.doesNotMatch((await notifications.textContent()) ?? "", /macOS|Native pop-ups|no web app is open/);
    await assertContained(soundPage, notifications, "iPhone notification panel");
    await report.scrollIntoViewIfNeeded();
    await assertContained(soundPage, report, "iPhone notification test result");
    await soundPage.screenshot({ path: join(shots, "mobile-iphone-notification-test.png") });
    result.iphoneControls = { deviceVolume: true, notificationTest: true, serverActionsHidden: true };
    await soundPage.close();
  }
} catch (error) {
  stage("failure-diagnostics");
  for (const context of browser.contexts())
    for (const page of context.pages()) {
      await page.screenshot({ path: join(shots, "mobile-failure.png") }).catch(() => {});
      const geometry = await page.evaluate(() =>
        [...document.querySelectorAll(".viewport-popover,[data-footer-content] button")].map((element) => ({
          label: element.getAttribute("aria-label"),
          id: element.id,
          bounds: element.getBoundingClientRect().toJSON(),
          style: element.getAttribute("style"),
        })),
      );
      writeFileSync(join(shots, "mobile-failure.json"), JSON.stringify(geometry, null, 2));
    }
  throw error;
} finally {
  stage("browser-cleanup");
  for (const context of browser.contexts())
    for (const page of context.pages()) await page.unrouteAll({ behavior: "ignoreErrors" }).catch(() => {});
  await browser.close();
}
stage("complete");
writeFileSync(join(shots, "mobile-responsive.json"), `${JSON.stringify(result, null, 2)}\n`);
console.log(JSON.stringify(result));
