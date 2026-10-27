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
  { name: "short-portrait", width: 390, height: 667 },
  { name: "narrow-landscape", width: 844, height: 390 },
  { name: "wide-landscape", width: 932, height: 430 },
  { name: "short-landscape", width: 844, height: 320 },
];
const result = {
  engine: `Chromium ${browser.version()}`,
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
    assert.equal(keyboardViewport.visual, keyboardViewport.layout, `${spec.name}: emulated visual viewport should track the resized layout viewport`);
    assert.ok(keyboardComposerBox && keyboardComposerBox.y >= (keyboardViewport.offsetTop ?? 0), `${spec.name}: composer is above the visual viewport`);
    await page.screenshot({ path: join(shots, `mobile-${spec.name}-chat.png`) });
    result.cases.push({
      ...spec,
      viewport: facts,
      accountMenu: menuBox,
      targets: { navigation: navTarget, settings: settingsTarget },
      composer: composerBox,
      navigationSettingsChat: true,
      visualViewportAfterFocus,
      safeAreaPadding: safePadding,
      keyboardReducedViewportComposer: keyboardComposerBox,
      keyboardViewport,
      touch: context.pages()[0].viewportSize(),
    });
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
    route.fulfill({ json: { selected: sound.name, effective: sound.name, soundscapes: [sound], signals: {}, volume: 50, follow_theme: false, mute_in_meetings: false, duck_while_typing: false } }),
  );
  await soundPage.route("https://www.youtube-nocookie.com/embed/**", (route) =>
    route.fulfill({ contentType: "text/html", body: `<!doctype html><html><body style="margin:0"><video muted autoplay playsinline></video><script>const c=document.createElement('canvas');c.width=320;c.height=180;const x=c.getContext('2d');setInterval(()=>{x.fillStyle='#308090';x.fillRect(0,0,320,180)},40);const v=document.querySelector('video');v.srcObject=c.captureStream(25);v.play();</script></body></html>` }),
  );
  await soundPage.goto(url);
  await soundPage.getByRole("heading", { name: "Today" }).waitFor();
  await soundPage.locator("#splash").waitFor({ state: "hidden", timeout: 15_000 });
  await soundPage.getByRole("button", { name: "Expand navigation" }).click();
  await soundPage.getByRole("button", { name: "Collapse navigation" }).waitFor();
  await soundPage.getByRole("button", { name: "Play Mobile dock probe", exact: true }).click();
  const player = soundPage.getByRole("region", { name: "Mobile dock probe player" });
  await player.waitFor();
  assert.equal(await player.getAttribute("data-dock"), "docked", "wide-portrait: stream player should be docked in the sidebar");
  const playerBox = await player.boundingBox();
  const popOut = soundPage.getByRole("button", { name: "Pop the player out of the sidebar" });
  const popOutBox = await assertTouchTarget(popOut, "docked player pop-out control", "wide-portrait");
  assert.ok(
    playerBox && playerBox.x >= 0 && playerBox.y >= 0 && playerBox.x + playerBox.width <= 430 + 1 && playerBox.y + playerBox.height <= 932 + 1,
    `wide-portrait: docked player must fit viewport: ${JSON.stringify(playerBox)}`,
  );
  await popOut.tap();
  await soundPage.waitForFunction(() => document.querySelector('[data-dock="floating"]'));
  result.dockedPlayer = { viewport: { width: 430, height: 932 }, dockedBox: playerBox, popOutTarget: popOutBox, popOutUsable: true };
  await soundPage.close();
} finally {
  await browser.close();
}
writeFileSync(join(shots, "mobile-responsive.json"), `${JSON.stringify(result, null, 2)}\n`);
console.log(JSON.stringify(result));
