// Focused mobile/touch viewport proof for the app served by `hz ui` (tests/e2e/web_ui_test.py).
// This is Chromium emulation, not physical iPhone Safari. Usage: node e2e/mobile-responsive.mjs URL SCREENSHOT_DIR.
import assert from "node:assert/strict";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { chromium } from "playwright-core";

const [url, shots] = process.argv.slice(2);
mkdirSync(shots, { recursive: true });
const browser = await chromium.launch().catch(() => chromium.launch({ channel: "chrome" }));
const cases = [
  { name: "narrow-portrait", width: 360, height: 780 },
  { name: "wide-portrait", width: 430, height: 932 },
  { name: "narrow-landscape", width: 844, height: 390 },
  { name: "wide-landscape", width: 932, height: 430 },
];
const result = {
  engine: `Chromium ${browser.version()}`,
  evidence: "mobile/touch emulation; not physical iPhone Safari",
  cases: [],
};

async function viewportFacts(page, name) {
  const facts = await page.evaluate(() => {
    const root = document.documentElement;
    const shell = document.querySelector("#root > div");
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

try {
  for (const spec of cases) {
    const context = await browser.newContext({
      viewport: { width: spec.width, height: spec.height },
      screen: { width: spec.width, height: spec.height },
      isMobile: true,
      hasTouch: true,
      deviceScaleFactor: 2,
      isLandscape: spec.width > spec.height,
      colorScheme: "light",
    });
    const page = await context.newPage();
    await page.goto(url);
    await page.getByRole("heading", { name: "Today" }).waitFor();
    await page.locator("#splash").waitFor({ state: "hidden", timeout: 15_000 });
    await page.waitForFunction(() => !document.getElementById("root")?.hasAttribute("inert"), null, { timeout: 5000 });
    const facts = await viewportFacts(page, spec.name);

    // Main navigation remains touch reachable in the collapsed rail.
    const navigation = page.getByRole("navigation", { name: "Main" });
    const navLink = navigation.getByRole("link", { name: "Heinzelmännchen" });
    const navTarget = await assertTouchTarget(navLink, "navigation link", spec.name);
    await navLink.click();
    await page.getByRole("heading", { name: "Heinzelmännchen", exact: true }).waitFor();
    await page.screenshot({ path: join(shots, `mobile-${spec.name}-navigation.png`) });

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
    await page.getByRole("dialog", { name: "Settings" }).waitFor();
    await page.getByRole("searchbox", { name: "Filter settings" }).waitFor();
    await page.screenshot({ path: join(shots, `mobile-${spec.name}-settings.png`) });
    await page.getByRole("button", { name: "Close settings" }).click();

    // The home chat opens automatically. Check the composer is inside the screen and can be focused by touch.
    await page.goto(url);
    await page.getByRole("heading", { name: "Today" }).waitFor();
    const composer = page.getByRole("textbox", { name: "Your message" });
    await composer.waitFor();
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
    await page.screenshot({ path: join(shots, `mobile-${spec.name}-chat.png`) });
    result.cases.push({
      ...spec,
      viewport: facts,
      accountMenu: menuBox,
      targets: { navigation: navTarget, settings: settingsTarget },
      composer: composerBox,
      navigationSettingsChat: true,
      visualViewportAfterFocus,
      touch: context.pages()[0].viewportSize(),
    });
    await context.close();
  }
} finally {
  await browser.close();
}
writeFileSync(join(shots, "mobile-responsive.json"), `${JSON.stringify(result, null, 2)}\n`);
console.log(JSON.stringify(result));
