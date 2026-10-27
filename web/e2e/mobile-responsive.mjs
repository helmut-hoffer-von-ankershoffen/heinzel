// Focused mobile/touch viewport proof for the app served by `hz ui` (tests/e2e/web_ui_test.py).
// This is Chromium emulation, not physical iPhone Safari. Usage: node e2e/mobile-responsive.mjs URL SCREENSHOT_DIR.
import assert from "node:assert/strict";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { chromium, webkit } from "playwright-core";
import ts from "typescript";

const [url, shots] = process.argv.slice(2);
mkdirSync(shots, { recursive: true });
const useWebKit = process.env.PLAYWRIGHT_BROWSER === "webkit";
const browser = useWebKit
  ? await webkit.launch()
  : await chromium.launch().catch(() => chromium.launch({ channel: "chrome" }));
const cases = [
  { name: "narrow-portrait", width: 360, height: 780 },
  { name: "wide-portrait", width: 430, height: 932 },
  { name: "short-portrait", width: 390, height: 667 },
  { name: "narrow-landscape", width: 844, height: 390 },
  { name: "wide-landscape", width: 932, height: 430 },
  { name: "short-landscape", width: 844, height: 320 },
];
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
  assert.equal(facts.scrollOwners.main, "auto", `${name}: main screen must own vertical scrolling`);
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

async function showPage(page) {
  const tab = page.getByRole("tab", { name: "Page", exact: true });
  if (await tab.isVisible()) await tab.click();
}

async function footerPane(page, id, label) {
  const trigger = page.locator(`[data-footer-content="${id}"] button`).first();
  if (!(await trigger.isVisible())) {
    await page.locator('[data-footer-content="@more"] button').click();
  }
  await trigger.tap();
  return page.getByRole("region", { name: label, exact: true }).locator("..");
}

try {
  for (const spec of cases) {
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
    // Platform-specific CLI paths and provider names must wrap rather than widen their settings pane.
    await page.route("**/api/ai/access/list", async (route) => {
      const response = await route.fetch();
      const listing = await response.json();
      listing.options.push({
        name: "mobile-overflow-probe",
        title: "AnExtremelyLongProviderNameThatMustWrapOnAPhone",
        summary: "A provider with an installation command.",
        module: "ai.access",
        plugin: null,
        in_use: false,
        status: "not installed",
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
      headers: { Authorization: `Bearer ${new URL(url).hash.slice("#token=".length)}` },
    });
    assert.ok(expertResponse.ok(), "Advanced settings must be enabled in the isolated fixture");
    // Enum display labels come from providers and can exceed a native select's intrinsic phone width.
    await page.route("**/api/system/config/show", async (route) => {
      const response = await route.fetch();
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
    await page.goto(url);
    await page.getByRole("heading", { name: "Today" }).waitFor();
    await page.locator("#splash").waitFor({ state: "hidden", timeout: 15_000 });
    await page.waitForFunction(() => !document.getElementById("root")?.hasAttribute("inert"), null, { timeout: 5000 });
    const facts = await viewportFacts(page, spec.name);
    if (facts.shellClientHeight !== undefined) {
      assert.ok(facts.shellClientHeight <= facts.height, `${spec.name}: shell exceeds the viewport height`);
    }

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
    for (const destination of visitedRoutes) {
      await page.goto(`${new URL(destination, url).href}${new URL(url).hash}`);
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
    assert.ok(
      (await searchField.evaluate((element) => Number.parseFloat(getComputedStyle(element).fontSize))) >= 16,
      `${spec.name}: focused phone form text must not trigger Safari zoom`,
    );
    await page.screenshot({ path: join(shots, `mobile-${spec.name}-settings.png`) });
    const settingsPages = await settingsDialog
      .getByLabel("Settings page", { exact: true })
      .locator("option")
      .evaluateAll((options) => options.map((option) => ({ id: option.value, title: option.textContent.trim() })));
    assert.ok(settingsPages.length > 5, `${spec.name}: the complete settings registry must be exercised`);
    for (const settingsPage of settingsPages) {
      if (spec.width < 640)
        await settingsDialog.getByLabel("Settings page", { exact: true }).selectOption(settingsPage.id);
      else
        await settingsDialog
          .getByRole("navigation", { name: "Settings", exact: true })
          .getByRole("button", { name: settingsPage.title, exact: true })
          .click();
      await assertContained(page, settingsDialog, `${spec.name}: settings ${settingsPage.id}`);
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

    await page.getByRole("button", { name: "Close settings" }).click();

    // Every footer pane must fit, including the wide release notes and controls moved into More.
    for (const [id, label] of [
      ["@resources", "System resources"],
      ["@version", /^v/],
    ]) {
      const pane = await footerPane(page, id, label);
      await assertContained(page, pane, `${spec.name}: ${id}`);
      await page.screenshot({ path: join(shots, `mobile-${spec.name}-${id.slice(1)}.png`) });
      await page.mouse.click(spec.width - 4, 4);
    }
    await page.goto(url);
    await page.getByRole("heading", { name: "Today" }).waitFor();
    await page.getByRole("button", { name: /^Notifications/ }).click();
    const notifications = page.getByRole("region", { name: "Notifications", exact: true });
    await assertContained(page, notifications, `${spec.name}: notifications`);
    await page.getByRole("button", { name: "Close notifications" }).click();
    await page.getByRole("button", { name: "Search", exact: true }).click();
    await assertContained(page, page.getByRole("dialog"), `${spec.name}: search`);
    await page.keyboard.press("Escape");

    // The home chat opens automatically. Check the composer is inside the screen and can be focused by touch.
    await page.goto(url);
    await page.getByRole("heading", { name: "Today" }).waitFor();
    // Both sides of the workspace: full navigation, top menu, chat switcher and terminal menus/full screen.
    await page.getByRole("button", { name: "Expand navigation" }).click();
    await assertContained(
      page,
      page.getByRole("navigation", { name: "Main" }),
      `${spec.name}: expanded left navigation`,
    );
    await page.getByRole("button", { name: "Collapse navigation" }).click();
    await page.getByRole("button", { name: "Home", exact: true }).click();
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
    await showPage(page);
    await page.getByRole("button", { name: "Open a terminal", exact: true }).click();
    const terminal = page.getByRole("region", { name: "Terminal", exact: true });
    await assertContained(page, terminal, `${spec.name}: terminal`);
    await terminal.evaluate((element) => {
      window.mobileTerminal = element;
    });
    await page.evaluate(() => {
      history.pushState({}, "", "?panel=world-clock");
      dispatchEvent(new PopStateEvent("popstate"));
    });
    await assertContained(
      page,
      page.getByRole("region", { name: "World clock", exact: true }),
      `${spec.name}: world clock with chat and terminal open`,
    );
    await page.getByRole("tab", { name: "Chat", exact: true }).click();
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
    await page.unrouteAll({ behavior: "wait" });
    await context.close();
  }
  // The full sidebar retains its dock slot at a phone-sized width. Check the player itself and its controls fit and
  // remain actionable after a touch gesture; this says nothing about physical iOS Safari behavior.
  const soundPage = await browser.newPage({
    viewport: { width: 430, height: 932 },
    screen: { width: 430, height: 932 },
    isMobile: true,
    hasTouch: true,
    deviceScaleFactor: 2,
  });
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
  await soundPage.goto(url);
  await soundPage.getByRole("heading", { name: "Today" }).waitFor();
  await soundPage.locator("#splash").waitFor({ state: "hidden", timeout: 15_000 });
  await soundPage.getByRole("button", { name: "Expand navigation" }).click();
  await soundPage.getByRole("button", { name: "Collapse navigation" }).waitFor();
  await soundPage.getByRole("button", { name: "Choose a soundscape", exact: true }).click();
  await soundPage
    .getByRole("dialog", { name: "Sound" })
    .getByRole("button", { name: "Play Mobile dock probe", exact: true })
    .click();
  await soundPage.keyboard.press("Escape");
  const player = soundPage.getByRole("region", { name: "Mobile dock probe player" });
  await player.waitFor();
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
  await popOut.tap();
  await soundPage.waitForFunction(() => document.querySelector('[data-dock="floating"]'));
  await soundPage.setViewportSize({ width: 360, height: 400 });
  await assertContained(soundPage, player, "rotated floating player");
  result.dockedPlayer = {
    viewport: { width: 430, height: 932 },
    dockedBox: playerBox,
    popOutTarget: popOutBox,
    popOutUsable: true,
  };
  await soundPage.close();
} catch (error) {
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
  await browser.close();
}
writeFileSync(join(shots, "mobile-responsive.json"), `${JSON.stringify(result, null, 2)}\n`);
console.log(JSON.stringify(result));
