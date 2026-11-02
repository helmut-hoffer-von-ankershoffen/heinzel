// Automatic updates against a fake newer release (ADR 0116): countdown, reload, postpone, the popup's return, apply.
// node auto-update.mjs URL FOLDER    URL is the printed link; FOLDER is the fake release's (tests/fixtures/auto_update)

import { writeFileSync } from "node:fs";
import { chromium } from "playwright-core";
import { until } from "./until.mjs";

const [url, folder] = process.argv.slice(2);
const browser = await chromium.launch();
const result = {};
try {
  const page = await browser.newPage({ viewport: { width: 1100, height: 800 } });
  await page.goto(url);
  await page.getByRole("contentinfo").waitFor({ timeout: 30000 });
  const api = (path, method = "GET") =>
    page.evaluate(
      async ([path, method]) => {
        const headers = { Authorization: `Bearer ${sessionStorage.getItem("hz.token")}` };
        const response = await fetch(`/api/${path}`, { method, headers });
        return response.json();
      },
      [path, method],
    );
  const popup = page.getByRole("alertdialog");
  const seconds = async () => Number((await popup.textContent()).match(/in (\d+) s/)[1]);

  // Both switches are one setting, on by default: off in the footer's Updates pane shows off in Settings → Updates,
  // and on there shows on in the pane, without a reload.
  const checked = async (control, value) =>
    until(async () => (await control.getAttribute("aria-checked")) === value, {
      timeout: 15000,
      interval: 200,
      message: `The switch didn't show ${value}`,
    });
  // A fresh state folder opens the first-run wizard over the app: close it, as a person would.
  const wizard = page.getByRole("dialog", { name: "Set up hz" });
  await page.waitForTimeout(1500);
  if (await wizard.isVisible()) {
    await wizard.getByRole("button", { name: "Close setup" }).click();
    await wizard.waitFor({ state: "hidden", timeout: 15000 });
  }
  const updates = page.getByRole("button", { name: /^(Up to date|Update available|Updates)/ });
  await updates.click();
  const footerSwitch = page.getByRole("switch", { name: "Automatic updates" });
  await footerSwitch.waitFor({ timeout: 15000 });
  await checked(footerSwitch, "true");
  result.footerEnabled = await footerSwitch.isEnabled();
  await footerSwitch.click();
  await checked(footerSwitch, "false");
  await page.keyboard.press("Escape");
  // Open Settings → Updates in this window, as a link in the app does: no reload, the same query cache.
  await page.evaluate(() => {
    history.pushState(null, "", "/?settings=updates");
    dispatchEvent(new PopStateEvent("popstate"));
  });
  const settings = page.getByRole("dialog", { name: "Settings" });
  await settings.waitFor({ timeout: 15000 });
  const settingsSwitch = settings.getByRole("switch", { name: "Automatic updates" });
  await checked(settingsSwitch, "false");
  result.settingsShowsFooterOff = true;
  await settingsSwitch.click();
  await checked(settingsSwitch, "true");
  await page.keyboard.press("Escape");
  await settings.waitFor({ state: "hidden", timeout: 15000 });
  await updates.click();
  await checked(footerSwitch, "true");
  result.footerShowsSettingsOn = true;
  await page.keyboard.press("Escape");

  // Publish the fake release once the app is ready: a check finds it within seconds; the countdown starts at 30.
  writeFileSync(`${folder}/release`, "99.0.0\n");
  await popup.waitFor({ timeout: 60000 });
  result.text = await popup.textContent();
  result.first = await seconds();
  result.buttons = await popup.getByRole("button").allTextContents();
  await page.waitForTimeout(2500);
  result.ticking = await seconds();

  // The plan is the server's: a reload shows what is left, not a fresh 30.
  await page.reload();
  await popup.waitFor({ timeout: 20000 });
  result.afterReload = await seconds();

  // Postpone: the popup closes, the server moves the apply time one hour later.
  const before = await api("auto-update");
  await popup.getByRole("button", { name: "Postpone 1 hour" }).click();
  await popup.waitFor({ state: "hidden", timeout: 10000 });
  const postponed = await api("auto-update");
  result.postponed = postponed.phase;
  result.postponements = postponed.postponements;
  result.movedBySeconds = Math.round(
    (Date.parse(postponed.next_window_at) + 30000 - Date.parse(before.apply_at)) / 1000,
  );

  // Skip the hour on the server's clock: the popup returns for the next window.
  writeFileSync(`${folder}/clock`, String(3600));
  await popup.waitFor({ timeout: 30000 });
  result.returned = true;

  // Not clicked: the popup closes and the server applies, then restarts in place.
  const instance = (await api("lifecycle")).instance;
  await popup.waitFor({ state: "hidden", timeout: 60000 });
  await until(
    async () => {
      try {
        return (await api("lifecycle")).instance !== instance;
      } catch {
        return false;
      }
    },
    { timeout: 90000, interval: 500, message: "No new server answered after the automatic update" },
  );
  result.restarted = true;
  const message = page.getByRole("status").filter({ hasText: "updated automatically" });
  await message.waitFor({ timeout: 30000 });
  result.message = await message.textContent();
  result.signedIn = (await page.getByRole("contentinfo").count()) > 0;
  result.after = (await api("auto-update")).phase;
  console.log(JSON.stringify(result));
} catch (error) {
  console.log(JSON.stringify({ ...result, error: String(error) }));
  process.exitCode = 1;
} finally {
  await browser.close();
}
