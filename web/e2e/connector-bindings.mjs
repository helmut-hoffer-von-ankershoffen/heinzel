// Settings → Connectors: bind every connector capability to one plugin's adapters, then switch mail, calendar,
// documents and tracker to another plugin's and back; after each round a reload shows exactly what was saved.
// Documents takes several connectors at once (ADR 0161): a switch per adapter, saved as a list.
// The server refuses restarts (as `make web-dev` does), so each "Restart and apply" saves and says to restart.
import assert from "node:assert/strict";
import { chromium, webkit } from "playwright-core";

const url = new URL(process.argv[2]);
const [, , , engine, shots] = process.argv;
const browser = await (engine === "webkit" ? webkit : chromium).launch({ headless: true });
const context = await browser.newContext({ serviceWorkers: "block", viewport: { width: 1280, height: 900 } });
const page = await context.newPage();

const TITLES = {
  mail: "Mail",
  calendar: "Calendar",
  documents: "Documents",
  tracker: "Tracker",
  chat: "Chat",
  meetings: "Meetings",
  "code-host": "Code host",
  directory: "Directory",
};
const choice = (capability) => page.getByRole("combobox", { name: TITLES[capability], exact: true });
// The switches themselves, inside the row that is labelled Documents too.
const documents = () => page.locator('fieldset[aria-label="Documents"]');

async function openConnectors() {
  const connectors = new URL(url); // keeps the session token in the fragment
  connectors.search = "?settings=connectors";
  await page.goto(connectors.href);
  await page.locator("#splash").waitFor({ state: "hidden", timeout: 30000 });
  await choice("mail").waitFor({ timeout: 15000 });
  await page.getByText("Loading choices…").first().waitFor({ state: "detached", timeout: 15000 });
}

/** Read the checked switches and their labels from the same render, in display order. */
async function documentsOn() {
  return documents().evaluate((row) =>
    Array.from(row.querySelectorAll('input[type="checkbox"]:checked'), (box) =>
      (box.labels?.[0]?.textContent ?? "").split(" · ")[0].trim(),
    ),
  );
}

async function bind(capability, adapter) {
  const title = TITLES[capability];
  let row;
  if (capability === "documents") {
    // Switch the wanted one on first, then the others off, so it is never none in between.
    await documents()
      .getByRole("checkbox", { name: new RegExp(`^${adapter} ·`) })
      .check();
    for (const other of await documentsOn())
      if (other !== adapter)
        await documents()
          .getByRole("checkbox", { name: new RegExp(`^${other} ·`) })
          .uncheck();
    assert.deepEqual(await documentsOn(), [adapter]);
    row = page.locator("li").filter({ has: documents() });
  } else {
    await choice(capability).selectOption(adapter);
    // Staged, it shows as chosen until applied.
    assert.equal(await choice(capability).inputValue(), adapter);
    row = page.locator("li").filter({ has: choice(capability) });
  }
  await row.getByRole("button", { name: "Restart and apply", exact: true }).click();
  await page.getByRole("button", { name: "Restart and apply hz ui" }).click();
  await row
    .getByRole("status")
    .filter({ hasText: `Saved ${title}.` })
    .waitFor({ timeout: 15000 });
}

async function expectBound(expected) {
  await openConnectors();
  for (const [capability, adapter] of Object.entries(expected))
    if (capability === "documents") assert.deepEqual(await documentsOn(), [adapter], capability);
    else assert.equal(await choice(capability).inputValue(), adapter, capability);
}

try {
  await openConnectors();
  // Nothing configured: the only installed chat adapter serves chat while nothing is configured, and the choice says so.
  assert.equal(await choice("chat").inputValue(), "");
  await page
    .getByRole("option", { name: "not set: bridge serves it, the only one installed" })
    .first()
    .waitFor({ state: "attached" });

  const bridge = {
    mail: "bridge",
    calendar: "bridge",
    documents: "bridge-wiki",
    tracker: "bridge",
    chat: "bridge",
    meetings: "bridge",
    "code-host": "bridge",
    directory: "bridge",
  };
  for (const [capability, adapter] of Object.entries(bridge)) await bind(capability, adapter);
  await expectBound(bridge);
  if (shots) await page.screenshot({ path: `${shots}/connectors-${engine}.png`, fullPage: true });

  const switched = { mail: "other", calendar: "other", documents: "other", tracker: "other" };
  for (const [capability, adapter] of Object.entries(switched)) await bind(capability, adapter);
  await expectBound({ ...bridge, ...switched });

  const back = { mail: "bridge", documents: "bridge-docs" };
  for (const [capability, adapter] of Object.entries(back)) await bind(capability, adapter);
  const final = { ...bridge, ...switched, ...back };
  await expectBound(final);
  console.log(JSON.stringify({ ...final, documents: [final.documents] }));
} catch (error) {
  if (shots) await page.screenshot({ path: `${shots}/connectors-${engine}-failed.png`, fullPage: true });
  throw error;
} finally {
  await browser.close();
}
