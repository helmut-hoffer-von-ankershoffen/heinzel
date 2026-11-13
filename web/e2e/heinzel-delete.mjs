// Delete a retired Heinzel for good (ADR 0148): from the Retired list, through the typed-name dialog, on a phone
// and on a desktop, in the engine the driver names.
import assert from "node:assert/strict";
import { chromium, webkit } from "playwright-core";

const [supplied, engine, phoneName, desktopName] = process.argv.slice(2);
if (!supplied || !phoneName || !desktopName) throw new Error("Usage: heinzel-delete.mjs URL ENGINE PHONE DESKTOP");
const token = new URL(supplied).hash.match(/(?:^#|&)token=([^&]+)/)?.[1] ?? "";
const browser = await (engine === "webkit" ? webkit : chromium).launch({ headless: true });

async function deleteOne(name, layout) {
  const context = await browser.newContext({ serviceWorkers: "block", ...layout });
  const page = await context.newPage();
  try {
    const target = new URL(supplied);
    target.pathname = "/heinzel";
    await page.goto(target.href);
    await page.locator("#splash").waitFor({ state: "hidden", timeout: 30000 });
    const setup = page.getByRole("dialog");
    await setup.waitFor({ state: "visible", timeout: 5000 }).catch(() => {});
    if (await setup.isVisible().catch(() => false)) await setup.getByRole("button", { name: "Close setup" }).click();
    const pageTab = page.getByRole("tab", { name: "Page", exact: true });
    if (await pageTab.isVisible().catch(() => false)) await pageTab.click();
    const personal = page.getByRole("region", { name: "Personal Heinzels" });
    const retired = personal.locator("details").filter({ has: page.locator("summary", { hasText: /^Retired \(/ }) });
    await retired.waitFor({ timeout: 30000 });
    const remove = retired.getByRole("button", { name: `Delete ${name} for good` });
    if (!(await remove.isVisible())) await retired.locator("summary").click();
    const width = layout.viewport.width;
    const at = await remove.boundingBox();
    assert.ok(at && at.x >= 0 && at.x + at.width <= width, "Delete fits the screen");
    await remove.click();
    const dialog = page.getByRole("alertdialog", { name: `Delete ${name} for good?` });
    await dialog.waitFor({ timeout: 10000 });
    const box = await dialog.boundingBox();
    assert.ok(box && box.x >= 0 && box.x + box.width <= width, "the dialog fits the screen");
    await dialog.getByText("This cannot be undone.").waitFor();
    const go = dialog.getByRole("button", { name: "Delete for good" });
    assert.equal(await go.isDisabled(), true, "nothing is deleted before the name is typed");
    const field = dialog.getByLabel(`Type ${name} to confirm`);
    await field.fill(name.toLowerCase());
    assert.equal(await go.isDisabled(), true, "a different name does not delete");
    await field.fill(name);
    const deleted = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/heinzel/delete");
    await go.click();
    assert.equal((await deleted).status(), 200);
    await personal.getByText(`${name} is deleted.`).waitFor({ timeout: 10000 });
    assert.equal(await page.getByRole("alertdialog").count(), 0, "the dialog closes");
    assert.equal(await personal.getByRole("button", { name: `Delete ${name} for good` }).count(), 0);
  } finally {
    await context.close();
  }
}

try {
  await deleteOne(phoneName, { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  await deleteOne(desktopName, { viewport: { width: 1280, height: 900 } });
  console.log(JSON.stringify({ completed: true }));
} catch (error) {
  process.stderr.write(`${String(error).replaceAll(decodeURIComponent(token), "<redacted-session>")}\n`);
  process.exitCode = 1;
} finally {
  await browser.close();
}
