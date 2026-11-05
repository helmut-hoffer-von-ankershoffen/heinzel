import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "vitest";
import { disclosureDiagnostics, watchDisclosure } from "./disclosure-diagnostics.mjs";

const page = { evaluate: async (callback) => callback() };
const summaryLocator = () => ({
  evaluate: async (callback, label) => callback(document.querySelector("summary"), label),
});

// jsdom has no layout hit-testing; real Chromium/WebKit probes validate the geometry separately.
beforeEach(() => {
  document.elementFromPoint = () => document.querySelector("summary");
});

afterEach(() => {
  delete document.elementFromPoint;
  delete document.elementsFromPoint;
  window.__hzDisclosureDiagnostics?.stop();
  delete window.__hzDisclosureDiagnostics;
  document.body.replaceChildren();
});

test("a disclosure replaced after opening is distinguishable from the original node staying closed", async () => {
  document.body.innerHTML = "<details><summary>Private fixture text</summary><p>Private content</p></details>";
  await watchDisclosure(summaryLocator(), "reply-activity");
  const original = document.querySelector("details");
  original.open = true;
  await Promise.resolve();
  const replacement = original.cloneNode(true);
  replacement.open = false;
  original.replaceWith(replacement);
  await Promise.resolve();
  const [{ records }] = await disclosureDiagnostics(page);
  assert.ok(records.some((record) => record.open === true));
  const replaced = records.find((record) => !record.originalConnected);
  assert.ok(replaced);
  assert.notEqual(replaced.current.id, replaced.original.id);
  assert.equal(replaced.open, false);
  assert.doesNotMatch(JSON.stringify(records), /Private/);
});

test("a click elsewhere records its target without changing the closed disclosure", async () => {
  document.body.innerHTML = "<button>Elsewhere</button><details><summary>Fixture</summary></details>";
  await watchDisclosure(summaryLocator(), "recurring-turns");
  document.querySelector("button").click();
  const [{ records }] = await disclosureDiagnostics(page);
  const click = records.find((record) => record.kind === "click");
  assert.equal(click.target.tag, "BUTTON");
  assert.deepEqual(click.pointHits, []);
  assert.equal(click.open, false);
  assert.equal(click.originalConnected, true);
  assert.equal(click.current.id, click.original.id);
});

test("observations stay bounded and collecting them cannot replace a browser failure", async () => {
  document.body.innerHTML = "<details><summary>Fixture</summary></details>";
  await watchDisclosure(summaryLocator(), "recurring-turns");
  for (let i = 0; i < 100; i++) document.body.click();
  const [{ records }] = await disclosureDiagnostics(page);
  assert.equal(records.length, 64);
  const closed = {
    evaluate: async () => {
      throw new Error("browser closed");
    },
  };
  assert.deepEqual(await disclosureDiagnostics(closed), []);
});

test("opening and closing in one task retains the open mutation even when the final state is closed", async () => {
  document.body.innerHTML = "<details><summary>Fixture</summary></details>";
  await watchDisclosure(summaryLocator(), "reply-activity");
  const details = document.querySelector("details");
  details.open = true;
  details.open = false;
  await Promise.resolve();
  const [{ records }] = await disclosureDiagnostics(page);
  const changes = records.flatMap((record) => record.openChanges ?? []);
  assert.deepEqual(
    changes.map((change) => change.wasOpen),
    [false, true],
  );
  assert.equal(details.open, false);
});

test("available pointer hit testing retains only bounded node identities", async () => {
  document.body.innerHTML = '<details><summary class="Private fixture class">Private fixture text</summary></details>';
  document.elementsFromPoint = () => Array(12).fill(document.querySelector("summary"));
  await watchDisclosure(summaryLocator(), "reply-activity");
  document.body.click();
  const [{ records }] = await disclosureDiagnostics(page);
  const click = records.find((record) => record.kind === "click");
  assert.equal(click.pointHits.length, 6);
  assert.ok(click.pointHits.every((hit) => hit.tag === "SUMMARY"));
  assert.doesNotMatch(JSON.stringify(records), /Private fixture/);
});
