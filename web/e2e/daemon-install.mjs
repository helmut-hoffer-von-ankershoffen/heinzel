// Mounted footer on a phone: the daemon's Install opens a confirmation, then shows real named steps, then the footer
// says what is true. The server's service manager is a fake (tests/e2e/daemon_install_ui_test.py).
import assert from "node:assert/strict";
import { readFileSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { join } from "node:path";
import { chromium, webkit } from "playwright-core";

const require = createRequire(import.meta.url);
const axe = readFileSync(require.resolve("axe-core/axe.min.js"), "utf8");
const url = new URL(process.argv[2]);
url.pathname = "/doctor";
const [, , , engine, directory, session] = process.argv;
const shots = process.env.HZ_SCREENSHOTS ?? directory;
const browser = await (engine === "webkit" ? webkit : chromium).launch({ headless: true });
const context = await browser.newContext({ serviceWorkers: "block", viewport: { width: 390, height: 844 } });
const page = await context.newPage();
const terminalRequests = [];
page.on("websocket", (socket) => {
  if (socket.url().endsWith("/api/terminal")) terminalRequests.push(socket.url());
});
const noHorizontalScroll = async () =>
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
const accessible = async (node) => {
  await page.evaluate(axe);
  const violations = await node.evaluate(async (element) =>
    (await window.axe.run(element)).violations.map(({ id }) => id),
  );
  assert.deepEqual(violations, []);
};
async function openFooter(name) {
  await page.getByRole("contentinfo").getByRole("button", { name }).click();
  return page.getByRole("region", { name: /^Daemon/ });
}
try {
  await page.goto(url.href);
  await page.locator("#splash").waitFor({ state: "hidden", timeout: 30000 });
  const setup = page.getByRole("dialog", { name: "Set up hz" });
  await setup.waitFor({ state: "visible", timeout: 5000 }).catch(() => {});
  if (await setup.isVisible().catch(() => false)) await setup.getByRole("button", { name: "Close setup" }).click();
  const pageTab = page.getByRole("tab", { name: "Page", exact: true });
  if (await pageTab.isVisible().catch(() => false)) await pageTab.click();

  // Not installed: the pane says what installing does, and Install is an operation, not a terminal command.
  let pane = await openFooter(/^Daemon not installed/);
  await pane.getByText("Keeps your Heinzelmännchen working while the app is closed").waitFor();
  await pane.getByRole("button", { name: "Install" }).click();
  const dialog = page.getByRole("alertdialog", { name: "Keep my Heinzelmännchen working while the app is closed." });
  await dialog.waitFor();
  assert.match((await dialog.textContent()) ?? "", /registers a background service/);
  await accessible(dialog);
  await noHorizontalScroll();
  const box = await dialog.boundingBox();
  assert.ok(box && box.x >= 0 && box.x + box.width <= 390, `dialog fits the phone: ${JSON.stringify(box)}`);
  await dialog.getByRole("button", { name: "Install background service" }).dblclick();

  if (session === "Background") {
    const alert = dialog.getByRole("alert");
    await alert.waitFor();
    assert.match((await alert.textContent()) ?? "", /desktop session/);
    assert.match((await alert.textContent()) ?? "", /Nothing was changed/);
    await dialog.getByRole("button", { name: "Try again" }).waitFor();
    await accessible(dialog);
    await noHorizontalScroll();
    await dialog.getByRole("button", { name: "Close" }).click();
    assert.deepEqual(terminalRequests, []);
    console.log(JSON.stringify({ session }));
  } else {
    // Held at the service manager: the steps so far are real, the button cannot be pressed again, nothing cancels.
    const steps = dialog.getByRole("list", { name: "Steps" });
    await steps.getByText("Loading it into launchd: in progress").waitFor();
    await steps.getByText("Writing the service definition: done").waitFor();
    assert.equal(await dialog.getByRole("button", { name: "Installing the background service" }).isDisabled(), true);
    assert.equal(await dialog.getByRole("button", { name: /Cancel|Close/ }).count(), 0);
    assert.equal(await dialog.getByRole("progressbar").getAttribute("max"), "5");
    await accessible(dialog);
    await noHorizontalScroll();
    await page.screenshot({ path: join(shots, `install-running-${engine}.png`) });
    writeFileSync(join(directory, "release"), "");
    await dialog.getByText("Installed. Its first check comes within 5 minutes.").waitFor();
    await steps.getByText("Setting up desktop notifications: done").waitFor();
    await accessible(dialog);
    await page.screenshot({ path: join(shots, `install-done-${engine}.png`) });
    await dialog.getByRole("button", { name: "Close" }).click();

    // The footer follows the real state: starting, with the first check named, and no restart advice.
    pane = await openFooter(/^Daemon starting/);
    await pane.getByText("Waiting for its first check").waitFor();
    assert.equal(await pane.getByRole("button", { name: "Restart daemon" }).count(), 0);
    assert.doesNotMatch((await pane.textContent()) ?? "", /restart|Unit records|unknown/i);
    await pane.getByRole("button", { name: "Uninstall" }).click();
    const removal = page.getByRole("alertdialog", { name: "Stop the background service?" });
    await removal.getByRole("button", { name: "Uninstall" }).click();
    await removal.getByText("Removed. Nothing starts at login any more.").waitFor();
    await removal.getByRole("button", { name: "Close" }).click();
    await openFooter(/^Daemon not installed/);
    assert.deepEqual(terminalRequests, []);
    console.log(JSON.stringify({ session }));
  }
} finally {
  await browser.close();
}
