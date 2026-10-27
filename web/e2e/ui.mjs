// Drive the app `hz ui` serves in Chromium (tests/e2e/web_ui_test.py): open the session URL, check the doctor
// screen, run axe on every screen, collect console errors (a CSP violation is one), and take screenshots; then the
// splash: with the API slowed down in light and dark, and with hz gone, where it turns into the fix.
// Usage: node e2e/ui.mjs URL SCREENSHOT_DIR   (prints one JSON object)
import { mkdirSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { join } from "node:path";
import { chromium } from "playwright-core";
import { verifyFooter } from "./footer.mjs";
import { verifyLayers } from "./layers.mjs";
import { verifyNavigation } from "./navigation.mjs";

const [url, shots] = process.argv.slice(2);
const base = new URL(url).origin;
const require = createRequire(import.meta.url);
const axeSource = readFileSync(require.resolve("axe-core/axe.min.js"), "utf8");
const tags = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];
mkdirSync(shots, { recursive: true });

async function launch() {
  try {
    return await chromium.launch();
  } catch {
    return await chromium.launch({ channel: "chrome" }); // a machine with Chrome but no downloaded Chromium
  }
}

const browser = await launch();
const result = { errors: [], violations: {}, doctor: "", screens: [], splash: {} };
const viewport = { width: 1440, height: 900 };
const API = "**/api/**";

async function axeViolations(page) {
  // Through the DevTools protocol, which the page's CSP doesn't govern; the page itself loads nothing extra.
  await page.evaluate(axeSource);
  const found = await page.evaluate((runOnly) => window.axe.run(document, { runOnly }), { type: "tag", values: tags });
  return found.violations.map(
    (violation) =>
      `${violation.id}: ${violation.help} @ ${violation.nodes
        .map((node) => node.target.join(" "))
        .slice(0, 3)
        .join(" | ")}`,
  );
}

/** The splash, in a context of its own: its aborted requests are meant to fail, so they aren't the app's errors. A CSP
 * violation by its inline script still shows in the main context, which loads the same page. */
async function splash() {
  for (const colorScheme of ["light", "dark"]) {
    const context = await browser.newContext({ viewport, colorScheme });
    const page = await context.newPage();
    // hz holds every answer until the splash was seen, then has to let it go by itself.
    let release = () => undefined;
    const held = new Promise((resolve) => {
      release = resolve;
    });
    await page.route(API, async (route) => {
      await held;
      await route.continue().catch(() => undefined);
    });
    await page.goto(url);
    await page.getByText("Connecting to hz…").waitFor();
    await page.screenshot({ path: join(shots, `splash-${colorScheme}.png`) });
    result.splash[colorScheme] = {
      line: await page.locator("#splash-line").textContent(),
      background: await page.locator("#splash").evaluate((element) => getComputedStyle(element).backgroundColor),
    };
    release();
    await page.unroute(API);
    await page.locator("#splash").waitFor({ state: "hidden", timeout: 15_000 });
    await page.getByRole("heading", { name: "Today" }).waitFor();
    result.splash[colorScheme].gone = true;
    await context.close();
  }
  // hz isn't running: after a few seconds, the fix instead of a spinner.
  const context = await browser.newContext({ viewport, colorScheme: "light" });
  const page = await context.newPage();
  await page.route(API, (route) => route.abort("connectionrefused"));
  await page.goto(url);
  await page.getByRole("alert").waitFor({ timeout: 15_000 });
  await page.getByRole("button", { name: /Copy the command/ }).waitFor();
  result.splash.error = await page.getByRole("alert").textContent();
  result.violations["splash-error"] = await axeViolations(page);
  await page.screenshot({ path: join(shots, "splash-error.png") });
  await context.close();
}
try {
  const context = await browser.newContext({ viewport, colorScheme: "light" });
  const page = await context.newPage();
  page.on("console", (message) => {
    if (message.type() === "error") result.errors.push(message.text());
  });
  page.on("pageerror", (error) => result.errors.push(String(error)));
  // The console names no URL for a failed request; this does.
  page.on("response", (response) => {
    if (response.status() >= 400) result.errors.push(`${response.status()} ${new URL(response.url()).pathname}`);
  });

  // This test server runs with plugins disabled. Give the optional clock a stable capability fixture so the
  // shared-layer test can exercise its overlay without depending on installed plugins on the test machine.
  await page.route("**/api/system/info", (route) =>
    route.fulfill({ json: { version: "0.7.2", modules: [{ qualname: "clock" }] } }),
  );
  await page.route("**/api/clock", (route) =>
    route.fulfill({
      json: [{ zone: "Europe/Berlin", hours: [9, 18], working: true, time: "12:00:00" }],
    }),
  );

  async function audit(name) {
    // The splash fades over the app after every load; audited mid-fade, the app's text is measured through it.
    await page.locator("#splash").waitFor({ state: "hidden" });
    result.violations[name] = await axeViolations(page);
    await page.screenshot({ path: join(shots, `${name}.png`) });
    result.screens.push(name);
  }

  await page.goto(url);
  await page.getByRole("heading", { name: "Today" }).waitFor(); // Home is the chat, with Today on top (ADR 0045)
  // The setup wizard, as Set up hz in the account menu opens it (it opens by itself only while hz lacks your role).
  const wizard = page.getByRole("dialog", { name: "Set up hz" });
  await page.goto(`${base}/?onboarding=`);
  await wizard.waitFor();
  for (let step = 1; step <= 7; step++) {
    await wizard.getByText(`Step ${step} of 7`).waitFor();
    await page.waitForFunction(() => !document.body.textContent?.includes("Loading…"));
    // Rendering settings is not enough: startup deliberately keeps the app inert until the splash leaves. A fast
    // backend can render this input before that minimum display time; keyboard interaction must wait for readiness.
    await page.locator("#splash").waitFor({ state: "hidden", timeout: 15_000 });
    await page.waitForFunction(() => !document.getElementById("root")?.hasAttribute("inert"), null, { timeout: 5000 });
    await audit(`onboarding-${step}`);
    if (step < 7) await wizard.getByRole("button", { name: "Next" }).click();
    await page.mouse.move(0, 0); // audit the resting state, not the button under the pointer
  }
  await page.keyboard.press("Escape");
  await wizard.waitFor({ state: "detached" });
  // Doctor is hz itself, not work: it is in the account menu, not the navigation.
  await page.getByRole("button", { name: /Mira/ }).click();
  await page.getByRole("menuitem", { name: "Check this machine" }).click();
  await page.getByRole("heading", { name: "Doctor" }).waitFor();
  await page.getByText("Heinzelmännchen is").waitFor();
  await page.locator("tbody tr").first().waitFor();
  result.doctor = (await page.locator("main").textContent()) ?? "";
  await audit("doctor");

  for (const [name, path, heading] of [
    ["home", "/", "Today"],
    ["settings", "/settings", "Settings"],
    ["heinzel", "/heinzel", "Heinzelmännchen"],
    ["contracts", "/contracts", "Workflow"],
    ["team-graph", "/graph", "Team graph"],
    ["team", "/team", "Team roster"],
    ["artifact-types", "/inventory/types", "Types"],
  ]) {
    await page.goto(`${base}${path}`);
    await page.getByRole("heading", { name: heading, exact: true }).waitFor();
    // Not "networkidle": the notice stream keeps a request open for as long as the app is (ADR 0046). The screen is
    // ready once nothing says it is loading.
    await page.waitForFunction(() => !document.body.textContent?.includes("Loading…"));
    await page.locator("#splash").waitFor({ state: "hidden", timeout: 15_000 });
    await page.waitForFunction(() => !document.getElementById("root")?.hasAttribute("inert"), null, { timeout: 5000 });
    if (name === "settings") {
      const search = page.getByRole("searchbox", { name: "Filter settings" });
      await page.waitForFunction((input) => input === document.activeElement, await search.elementHandle(), {
        timeout: 5000,
      });
      const oneFocusIndicator = async () => {
        try {
          // React applies the ring after its keyboard event. Wait for that committed visual state, as we do for
          // other async UI, rather than reading the previous render immediately after pressSequentially/Shift+Tab.
          await page.waitForFunction(
            (input) =>
              input === document.activeElement &&
              getComputedStyle(input).outlineStyle === "none" &&
              getComputedStyle(input).boxShadow === "none" &&
              getComputedStyle(input.closest("label")).boxShadow !== "none",
            await search.elementHandle(),
            { timeout: 5000 },
          );
        } catch {
          const focus = await search.evaluate((input) => ({
            active: input === document.activeElement,
            appInert: document.getElementById("root")?.hasAttribute("inert"),
            activeTag: document.activeElement?.tagName,
            activeLabel: document.activeElement?.getAttribute("aria-label"),
            inputClass: input.className,
            parentClass: input.closest("label").className,
            inputOutline: getComputedStyle(input).outlineStyle,
            inputShadow: getComputedStyle(input).boxShadow,
            parentShadow: getComputedStyle(input.closest("label")).boxShadow,
          }));
          result.errors.push(`Settings search must have one parent focus indicator: ${JSON.stringify(focus)}`);
        }
      };
      await search.pressSequentially("volume");
      await oneFocusIndicator();
      await page.keyboard.press("Tab");
      await page.keyboard.press("Shift+Tab");
      await oneFocusIndicator();
      await search.fill("");
    }
    await audit(name);
  }

  await page.keyboard.press("ControlOrMeta+k");
  await page.getByRole("combobox").fill("weekly");
  await page.screenshot({ path: join(shots, "search.png") });
  await page.keyboard.press("Escape");

  await page.emulateMedia({ colorScheme: "dark" });
  await page.getByRole("button", { name: "Switch between light and dark" }).click();
  await page.goto(`${base}/doctor`);
  await page.locator("tbody tr").first().waitFor();
  await audit("doctor-dark");
  result.layers = await verifyLayers(page, shots, true);
  result.navigation = await verifyNavigation(page, url, shots);
  result.footer = await verifyFooter(page, shots);
  await splash();
} finally {
  await browser.close();
}
console.log(JSON.stringify(result));
