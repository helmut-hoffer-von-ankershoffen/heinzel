import assert from "node:assert/strict";
import { afterEach, test } from "vitest";
import { readWorkshopReload, watchWorkshopReload } from "./workshop-reload-diagnostics.mjs";

const page = { evaluate: async (callback) => callback() };

function fixture() {
  document.body.innerHTML = `
    <div id="root" class="workspace-scroll">
      <div><main><p>Private page content</p></main></div>
      <div class="workspace-panel" data-active="page">
        <button role="tab" aria-controls="Private-id-chat" aria-selected="false">Chat</button>
        <div id="Private-id-chat" role="tabpanel" hidden>Private chat content</div>
      </div>
    </div>`;
}

afterEach(async () => {
  await readWorkshopReload(page);
  document.body.replaceChildren();
});

test("distinguishes a lost pointer hit and a selection reset without serializing content", async () => {
  fixture();
  const tab = document.querySelector("button");
  const workspace = document.querySelector(".workspace-panel");
  const panel = document.querySelector('[role="tabpanel"]');
  assert.equal(await watchWorkshopReload(page), true);
  const event = (target, type) => target.dispatchEvent(new Event(type, { bubbles: true }));
  event(tab, "pointerdown");
  event(document.querySelector("main p"), "pointerup");
  event(tab, "click");
  workspace.dataset.active = "chat";
  tab.setAttribute("aria-selected", "true");
  panel.hidden = false;
  workspace.dataset.active = "Private contributed panel";
  tab.setAttribute("aria-selected", "false");
  panel.hidden = true;
  await Promise.resolve();

  const records = await readWorkshopReload(page);
  assert.deepEqual(
    records.filter((entry) => entry.hit).map(({ kind, hit, trusted }) => [kind, hit, trusted]),
    [
      ["pointerdown", "chat-tab", false],
      ["pointerup", "page", false],
      ["click", "chat-tab", false],
    ],
  );
  assert.ok(records.some((entry) => entry.previousActive === "chat"));
  assert.ok(records.some((entry) => entry.previousSelected === true));
  assert.ok(records.some((entry) => entry.previousHidden === false));
  assert.equal(records.at(-1).active, "other");
  assert.equal(records.at(-1).panelHidden, true);
  assert.doesNotMatch(JSON.stringify(records), /Private/);
  assert.equal(await readWorkshopReload(page), null);
});

test("bounds observations and keeps an unavailable probe optional", async () => {
  assert.equal(await watchWorkshopReload(page), false);
  fixture();
  assert.equal(await watchWorkshopReload(page), true);
  for (let i = 0; i < 100; i++) document.querySelector("button").click();
  const records = await readWorkshopReload(page);
  assert.equal(records.length, 25);
  assert.equal(records[0].kind, "before");
  assert.equal(records.at(-1).kind, "failure");

  const closed = { evaluate: async () => Promise.reject(new Error("Private diagnostic error")) };
  assert.equal(await watchWorkshopReload(closed), false);
  assert.equal(await readWorkshopReload(closed), null);
});
