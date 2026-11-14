// Settings → Connectors with two connector plugins and a company's two layers installed, while the plugin list is
// slow (it can take seconds): until it answers, no plugin page is placed and Connectors says it is looking; once it
// answers, the connectors are cards on Connectors and the layers are pages under Organisation, never under Preferences.
import assert from "node:assert/strict";
import { chromium, webkit } from "playwright-core";

const url = new URL(process.argv[2]);
const [, , , engine, shots] = process.argv;
const browser = await (engine === "webkit" ? webkit : chromium).launch({ headless: true });
const context = await browser.newContext({ serviceWorkers: "block", viewport: { width: 1280, height: 900 } });
const page = await context.newPage();

/** The plugins as `hz system plugin list` reports them for this test's modules, each from its own distribution. */
const info = (name, extra) => ({
  name,
  distribution: `heinzel-${name}`,
  version: "1.0.0",
  modules: [name],
  summary: `Test-only ${name}.`,
  source_kind: "git",
  first_party: false,
  enabled: true,
  error: null,
  capabilities: [],
  layers: [],
  kinds: [],
  themes: [],
  playbooks: [],
  ...extra,
});
const STACK = [
  info("bridge", { capabilities: ["bridge:mail", "bridge:chat"], kinds: ["connector"] }),
  info("other", { capabilities: ["other:mail"], kinds: ["connector"] }),
  info("acme", { layers: [{ module: "acme", parent: null, site: null, label: "company" }] }),
  info("acme-lisbon", { layers: [{ module: "acme-lisbon", parent: "acme", site: null, label: "site" }] }),
];

let release;
const answered = new Promise((resolve) => {
  release = resolve;
});
await page.route("**/api/system/plugin/list**", async (route) => {
  const response = await route.fetch();
  const listed = await response.json();
  await answered;
  await route.fulfill({
    response,
    json: [...listed.filter((item) => !STACK.some((s) => s.name === item.name)), ...STACK],
  });
});

/** The sidebar's pages, by heading. */
const sidebar = () =>
  page.evaluate(() => {
    const groups = {};
    let heading = "";
    const column = document.querySelector('nav[aria-label="Settings"] > div:last-child');
    for (const element of column?.children ?? []) {
      if (element.tagName === "P") heading = element.textContent.trim();
      else if (element.tagName === "BUTTON") (groups[heading] ??= []).push(element.textContent.trim());
    }
    return groups;
  });

try {
  const connectors = new URL(url); // keeps the session token in the fragment
  connectors.search = "?settings=connectors";
  await page.goto(connectors.href);
  await page.locator("#splash").waitFor({ state: "hidden", timeout: 30000 });
  const looking = page.getByRole("status").filter({ hasText: "Looking for installed connectors…" });
  await looking.waitFor({ timeout: 15000 });
  assert.equal(await page.getByText("No connector installed yet").count(), 0);
  const early = await sidebar();
  for (const name of ["Bridge", "Other", "ACME", "ACME Lisbon"])
    assert.ok(!Object.values(early).flat().includes(name), `${name} placed before the plugin list answered`);
  if (shots) await page.screenshot({ path: `${shots}/connector-pages-loading-${engine}.png` });

  release();
  const card = (name) => page.locator("li > button").filter({ hasText: name });
  await card("Bridge").waitFor({ timeout: 15000 });
  await card("Other").waitFor();
  await looking.waitFor({ state: "detached" });
  const cards = await page.locator("li > button").allTextContents();
  const later = await sidebar();
  assert.deepEqual(
    later.Preferences?.filter((name) => ["Bridge", "Other", "ACME", "ACME Lisbon"].includes(name)) ?? [],
    [],
  );
  if (shots) await page.screenshot({ path: `${shots}/connector-pages-${engine}.png` });
  console.log(
    JSON.stringify({
      loading: "Looking for installed connectors…",
      connectors: cards.flatMap((card) => ["Bridge", "Other"].filter((name) => card.trim().startsWith(name))),
      organisation: (later.Organisation ?? []).filter((name) => name.startsWith("ACME")),
    }),
  );
} catch (error) {
  if (shots) await page.screenshot({ path: `${shots}/connector-pages-${engine}-failed.png`, fullPage: true });
  throw error;
} finally {
  await browser.close();
}
