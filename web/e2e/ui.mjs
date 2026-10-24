// Drive the app `hz ui` serves in Chromium (tests/e2e/web_ui_test.py): open the session URL, check the doctor
// screen, run axe on every screen, collect console errors (a CSP violation is one), and take screenshots.
// Usage: node e2e/ui.mjs URL SCREENSHOT_DIR   (prints one JSON object)
import { mkdirSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { join } from "node:path";
import { chromium } from "playwright-core";

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
const result = { errors: [], violations: {}, doctor: "", screens: [] };
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: "light" });
  const page = await context.newPage();
  page.on("console", (message) => {
    if (message.type() === "error") result.errors.push(message.text());
  });
  page.on("pageerror", (error) => result.errors.push(String(error)));
  // The console names no URL for a failed request; this does.
  page.on("response", (response) => {
    if (response.status() >= 400) result.errors.push(`${response.status()} ${new URL(response.url()).pathname}`);
  });

  async function audit(name) {
    // Through the DevTools protocol, which the page's CSP doesn't govern; the page itself loads nothing extra.
    await page.evaluate(axeSource);
    const found = await page.evaluate((runOnly) => window.axe.run(document, { runOnly }), {
      type: "tag",
      values: tags,
    });
    result.violations[name] = found.violations.map(
      (violation) =>
        `${violation.id}: ${violation.help} @ ${violation.nodes
          .map((node) => node.target.join(" "))
          .slice(0, 3)
          .join(" | ")}`,
    );
    await page.screenshot({ path: join(shots, `${name}.png`) });
    result.screens.push(name);
  }

  await page.goto(url);
  await page.getByRole("heading", { name: "Today" }).waitFor(); // Home is the chat, with Today on top (ADR 0045)
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Doctor" }).click();
  await page.getByRole("heading", { name: "Doctor" }).waitFor();
  await page.getByText("Heinzelmännchen is").waitFor();
  await page.locator("tbody tr").first().waitFor();
  result.doctor = (await page.locator("main").textContent()) ?? "";
  await audit("doctor");

  for (const [name, path, heading] of [
    ["home", "/", "Today"],
    ["settings", "/settings", "Settings"],
    ["contracts", "/contracts", "Workflow"],
    ["team", "/team", "Team roster"],
    ["asset-types", "/inventory/types", "Asset types"],
  ]) {
    await page.goto(`${base}${path}`);
    await page.getByRole("heading", { name: heading, exact: true }).waitFor();
    // Not "networkidle": the notice stream keeps a request open for as long as the app is (ADR 0046). The screen is
    // ready once nothing says it is loading.
    await page.waitForFunction(() => !document.body.textContent?.includes("Loading…"));
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
} finally {
  await browser.close();
}
console.log(JSON.stringify(result));
