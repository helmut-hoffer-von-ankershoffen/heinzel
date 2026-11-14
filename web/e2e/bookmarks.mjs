// Bookmarks end to end against a real `hz ui`: the manager (folder, add, move, drag, remove and Undo), the
// navigation's tree (open, remembered, keyboard, a click opens the page), the desktop bridge, and a phone.
// Usage: node e2e/bookmarks.mjs URL SCREENSHOT_DIR
import assert from "node:assert/strict";
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { chromium } from "playwright-core";

const [url, shots] = process.argv.slice(2);
if (!url || !shots) throw new Error("Usage: node e2e/bookmarks.mjs URL SCREENSHOT_DIR");
mkdirSync(shots, { recursive: true });
const manager = `${new URL("/bookmarks", url).href}${new URL(url).hash}`;
const browser = await chromium.launch({ headless: true });
const result = {};

/** A context whose bookmarked sites answer locally: nothing here reaches the network. */
async function context(options) {
  const made = await browser.newContext({ serviceWorkers: "block", ...options });
  await made.route(/^https:\/\/example\.(com|org)\//, (route) =>
    route.fulfill({ contentType: "text/html", body: "<title>Bookmarked</title><p>Bookmarked page</p>" }),
  );
  return made;
}
/** The app draws again once its splash leaves: what was typed or focused before that is lost. */
const settled = (page) => page.locator("#splash").waitFor({ state: "hidden", timeout: 15_000 });
const rows = (page) => page.getByRole("list", { name: "Bookmarks and folders" }).locator("li[data-entry]");
/** Each row's name with its indent in rem: the order and the nesting as the person sees them. */
const shape = (page) =>
  rows(page).evaluateAll((items) =>
    items.map((item) => [
      item.querySelector("button[title], span.font-medium")?.textContent ?? "",
      Number.parseFloat(item.style.paddingLeft),
    ]),
  );
const row = (page, name) => rows(page).filter({ hasText: name }).first();
/** Wait until the tree shows exactly these items, or the focus sits on this one: a key press draws a frame later. */
const expectTree = (page, names) =>
  page.waitForFunction(
    (expected) =>
      JSON.stringify(
        [...document.querySelectorAll('[role="tree"][aria-label="Bookmarks"] [role="treeitem"]')].map(
          (item) => item.textContent,
        ),
      ) === JSON.stringify(expected),
    names,
    { timeout: 5000 },
  );
const expectFocus = (page, name) =>
  page.waitForFunction((expected) => document.activeElement?.textContent === expected, name, { timeout: 5000 });

try {
  // --- The manager, at desktop width, as the PWA (no desktop bridge) ---
  const desktop = await context({ viewport: { width: 1440, height: 900 } });
  const page = await desktop.newPage();
  await page.goto(manager);
  await page.getByRole("heading", { name: "Bookmarks" }).waitFor();
  await settled(page);
  await page.getByText("No bookmarks yet.").waitFor();
  assert.equal(await page.getByRole("tree", { name: "Bookmarks" }).count(), 0, "no tree without bookmarks");

  await page.getByRole("textbox", { name: "Folder name" }).fill("Work");
  await page.getByRole("button", { name: "New folder" }).click();
  await row(page, "Work").waitFor();
  const address = page.getByRole("textbox", { name: "Address of the new bookmark" });
  await address.fill("example.com/docs");
  await page.getByRole("button", { name: "Add bookmark" }).click();
  await row(page, "example.com").waitFor();
  await address.fill("https://example.org/");
  await page.getByRole("button", { name: "Add bookmark" }).click();
  await row(page, "example.org").waitFor();
  assert.deepEqual(await shape(page), [
    ["Work", 0.5],
    ["example.com", 0.5],
    ["example.org", 0.5],
  ]);
  result.created = true;

  // Move with the folder choice, then by dragging onto the folder, then reorder with the arrow button.
  await page.getByRole("combobox", { name: "Folder of example.com" }).selectOption({ label: "Work" });
  await page.waitForFunction(
    () =>
      document.querySelectorAll('ul[aria-label="Bookmarks and folders"] li[data-entry]')[1]?.style.paddingLeft ===
      "1.75rem",
  );
  await row(page, "example.org").dragTo(row(page, "Work"));
  await page.waitForFunction(
    () =>
      document.querySelectorAll('ul[aria-label="Bookmarks and folders"] li[data-entry]')[2]?.style.paddingLeft ===
      "1.75rem",
  );
  assert.deepEqual(await shape(page), [
    ["Work", 0.5],
    ["example.com", 1.75],
    ["example.org", 1.75],
  ]);
  await page.getByRole("button", { name: "Move example.org up" }).click();
  await page.waitForFunction(() =>
    document
      .querySelectorAll('ul[aria-label="Bookmarks and folders"] li[data-entry]')[1]
      ?.textContent?.includes("example.org"),
  );
  assert.deepEqual(await shape(page), [
    ["Work", 0.5],
    ["example.org", 1.75],
    ["example.com", 1.75],
  ]);
  result.moved = true;

  // Rename, then remove with Undo: the entry returns to its place.
  await page.getByRole("button", { name: "Rename example.org" }).click();
  await page.getByRole("textbox", { name: "Name", exact: true }).fill("News");
  await page.getByRole("button", { name: "Save" }).click();
  await row(page, "News").waitFor();
  await page.getByRole("button", { name: "Remove News" }).click();
  const note = page.getByRole("status").filter({ hasText: "Removed “News”." });
  await note.waitFor();
  assert.equal(await row(page, "News").count(), 0);
  await note.getByRole("button", { name: "Undo" }).click();
  await row(page, "News").waitFor();
  assert.deepEqual(await shape(page), [
    ["Work", 0.5],
    ["News", 1.75],
    ["example.com", 1.75],
  ]);
  result.undone = true;
  await page.screenshot({ path: join(shots, "bookmarks-manager.png") });

  // --- The navigation's tree ---
  const tree = page.getByRole("tree", { name: "Bookmarks" });
  await tree.waitFor();
  await expectTree(page, ["Work"]);
  const work = tree.getByRole("treeitem", { name: "Work" });
  assert.equal(await work.getAttribute("aria-expanded"), "false");
  await work.click();
  assert.equal(await work.getAttribute("aria-expanded"), "true");
  await expectTree(page, ["Work", "News", "example.com"]);
  await page.reload();
  await settled(page);
  await page.getByRole("tree", { name: "Bookmarks" }).getByRole("treeitem", { name: "News" }).waitFor();
  result.remembered = true;

  // Keyboard: left folds, right opens and then enters, down moves, one tab stop, Enter opens the page.
  await work.focus();
  await page.keyboard.press("ArrowLeft");
  await expectTree(page, ["Work"]);
  await page.keyboard.press("ArrowRight");
  await expectTree(page, ["Work", "News", "example.com"]);
  await page.keyboard.press("ArrowRight");
  await expectFocus(page, "News");
  await page.keyboard.press("ArrowDown");
  await expectFocus(page, "example.com");
  assert.deepEqual(
    await tree.getByRole("treeitem").evaluateAll((items) => items.map((item) => item.tabIndex)),
    [-1, -1, 0],
  );
  await page.keyboard.press("End");
  await page.keyboard.press("Home");
  await expectFocus(page, "Work");
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("ArrowDown");
  const [byKey] = await Promise.all([desktop.waitForEvent("page"), page.keyboard.press("Enter")]);
  await byKey.waitForURL("https://example.com/docs");
  await byKey.close();
  result.keyboard = true;
  const [byClick] = await Promise.all([
    desktop.waitForEvent("page"),
    tree.getByRole("treeitem", { name: "News" }).click(),
  ]);
  await byClick.waitForURL("https://example.org/");
  assert.equal(await byClick.evaluate(() => window.opener), null, "the opened page gets no handle on the app");
  await byClick.close();
  result.pwaTab = true;
  await page.screenshot({ path: join(shots, "bookmarks-navigation.png") });
  await desktop.close();

  // --- The desktop app: a click asks the built-in browser for a tab and shows its panel; no window opens ---
  const app = await context({ viewport: { width: 1440, height: 900 } });
  const native = await app.newPage();
  await native.addInitScript(() => {
    window.__browser = [];
    const state = () => ({ status: "empty", url: "", tabs: [], selected: null, link: null, notice: null });
    window.heinzelDesktop = Object.freeze({
      lifecycle: () => Promise.resolve(null),
      browser: (request) => {
        if (request.action !== "state" && request.action !== "hide" && request.action !== "layout")
          window.__browser.push(request);
        return Promise.resolve(state());
      },
    });
  });
  let popups = 0;
  app.on("page", () => {
    popups += 1;
  });
  await native.goto(url);
  await settled(native);
  popups = 0;
  const nativeTree = native.getByRole("tree", { name: "Bookmarks" });
  await nativeTree.getByRole("treeitem", { name: "Work" }).click(); // a new profile: the folder is closed again
  await nativeTree.getByRole("treeitem", { name: "example.com" }).click();
  await native.getByRole("region", { name: "Browser" }).waitFor();
  assert.deepEqual(await native.evaluate(() => window.__browser.filter((request) => request.action === "new-tab")), [
    { action: "new-tab", url: "https://example.com/docs" },
  ]);
  assert.equal(popups, 0, "the desktop app opens no second window");
  result.desktopTab = true;
  await app.close();

  // --- A phone: the manager fits the width and works by touch; the tree is in the navigation drawer ---
  const phone = await context({ viewport: { width: 375, height: 812 }, hasTouch: true, isMobile: true });
  const small = await phone.newPage();
  await small.goto(manager);
  await settled(small);
  await row(small, "example.com").waitFor();
  const fits = () => small.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);
  assert.equal(await fits(), true, "the manager does not scroll sideways on a phone");
  const targets = await rows(small)
    .getByRole("button")
    .evaluateAll((buttons) =>
      buttons.map((button) => button.getBoundingClientRect()).map((box) => Math.min(box.width, box.height)),
    );
  assert.ok(Math.min(...targets) >= 24, `touch targets of at least 24 px, got ${Math.min(...targets)}`);
  await small.getByRole("button", { name: "Remove example.com" }).tap();
  const phoneNote = small.getByRole("status").filter({ hasText: "Removed “example.com”." });
  await phoneNote.getByRole("button", { name: "Undo" }).tap();
  await row(small, "example.com").waitFor();
  assert.equal(await fits(), true);
  await small.screenshot({ path: join(shots, "bookmarks-manager-phone.png") });
  await small.getByRole("button", { name: "Expand navigation" }).tap();
  const phoneTree = small.getByRole("tree", { name: "Bookmarks" });
  await phoneTree.waitFor();
  const phoneWork = phoneTree.getByRole("treeitem", { name: "Work" });
  if ((await phoneWork.getAttribute("aria-expanded")) !== "true") await phoneWork.tap();
  const [tapped] = await Promise.all([
    phone.waitForEvent("page"),
    phoneTree.getByRole("treeitem", { name: "example.com" }).tap(),
  ]);
  await tapped.waitForURL("https://example.com/docs");
  result.phone = true;
  await phone.close();
} finally {
  await browser.close();
}
console.log(JSON.stringify(result));
