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

test("scroll observations delegate exact arguments once and restore the original method", async () => {
  document.body.innerHTML =
    '<div class="workspace-scroll"><div><main id="content"></main></div><div class="workspace-panel" data-active="chat"><details><summary>Private fixture text</summary></details></div></div>';
  const outer = document.querySelector(".workspace-scroll");
  const panel = document.querySelector(".workspace-panel");
  Object.defineProperty(panel, "offsetTop", { value: 800 });
  Object.defineProperty(panel.previousElementSibling, "offsetHeight", { value: 800 });
  const calls = [];
  const marker = { privateResult: "never in diagnostic output" };
  const native = function (...args) {
    calls.push({ receiver: this, args });
    return marker;
  };
  outer.scrollTo = native;
  await watchDisclosure(summaryLocator(), "reply-activity");
  const options = { top: 400, behavior: "instant", privateField: "Private fixture argument" };
  assert.equal(outer.scrollTo(options), marker);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].receiver, outer);
  assert.equal(calls[0].args[0], options);
  const [{ records }] = await disclosureDiagnostics(page);
  const request = records.find((record) => record.kind === "scroll-request");
  assert.deepEqual(request.lastScrollRequest, { behavior: "instant", top: 400, before: 0 });
  assert.equal(request.workspaceState.selected, "chat");
  assert.equal(request.workspaceState.pageHeight, 800);
  assert.equal(request.workspaceState.panelTop, 800);
  assert.doesNotMatch(JSON.stringify(records), /Private|privateField|privateResult|never in/);
  window.__hzDisclosureDiagnostics.stop();
  assert.equal(outer.scrollTo, native);
});

test("focus observations are retained even when geometry is unchanged and selection is classified", async () => {
  document.body.innerHTML =
    '<div class="workspace-scroll"><div><main id="content"></main></div><div class="workspace-panel" data-active="private-contribution"><details><summary>Fixture</summary></details></div></div>';
  await watchDisclosure(summaryLocator(), "reply-activity");
  const summary = document.querySelector("summary");
  summary.dispatchEvent(new Event("focusin", { bubbles: true }));
  summary.dispatchEvent(new Event("focusin", { bubbles: true }));
  const [{ records }] = await disclosureDiagnostics(page);
  assert.equal(records.filter((record) => record.kind === "focusin").length, 2);
  assert.ok(records.every((record) => record.workspaceState.selected === "other"));
  assert.doesNotMatch(JSON.stringify(records), /private-contribution/);
});

test("observing a throwing scroll preserves the original exception", async () => {
  document.body.innerHTML =
    '<div class="workspace-scroll"><div></div><div class="workspace-panel"><details><summary>Fixture</summary></details></div></div>';
  const outer = document.querySelector(".workspace-scroll");
  const failure = new Error("Private native failure");
  outer.scrollTo = () => {
    throw failure;
  };
  await watchDisclosure(summaryLocator(), "reply-activity");
  assert.throws(
    () => outer.scrollTo({ top: 0, behavior: "smooth" }),
    (error) => error === failure,
  );
  const [{ records }] = await disclosureDiagnostics(page);
  assert.doesNotMatch(JSON.stringify(records), /Private native failure/);
});

test("native reveal observation is scoped, delegates exactly, omits text and restores the method", async () => {
  document.body.innerHTML =
    '<button>Private outside</button><div class="workspace-scroll"><div></div><div class="workspace-panel"><details><summary>Private inside</summary></details></div></div>';
  const previous = Element.prototype.scrollIntoView;
  const calls = [];
  const marker = {};
  const failure = new Error("Private reveal failure");
  const native = function (...args) {
    calls.push({ receiver: this, args });
    if (args[0] === false) throw failure;
    return marker;
  };
  Element.prototype.scrollIntoView = native;
  try {
    await watchDisclosure(summaryLocator(), "reply-activity");
    const options = { block: "nearest", inline: "nearest", privateField: "Private argument" };
    const summary = document.querySelector("summary");
    assert.equal(summary.scrollIntoView(options), marker);
    assert.equal(calls[0].receiver, summary);
    assert.equal(calls[0].args[0], options);
    assert.equal(document.querySelector("button").scrollIntoView(options), marker);
    assert.throws(
      () => summary.scrollIntoView(false),
      (error) => error === failure,
    );
    const [{ records }] = await disclosureDiagnostics(page);
    assert.equal(records.filter((record) => record.kind === "reveal-request").length, 2);
    assert.equal(records.filter((record) => record.kind === "reveal-return").length, 1);
    const reveal = records.find((record) => record.kind === "reveal-return");
    assert.equal(reveal.lastRevealRequest.argumentKind, "options");
    assert.equal(reveal.lastRevealRequest.target.tag, "SUMMARY");
    assert.doesNotMatch(JSON.stringify(records), /Private|privateField/);
    window.__hzDisclosureDiagnostics.stop();
    assert.equal(Element.prototype.scrollIntoView, native);
  } finally {
    window.__hzDisclosureDiagnostics?.stop();
    if (previous) Element.prototype.scrollIntoView = previous;
    else delete Element.prototype.scrollIntoView;
  }
});

test("scroll observation never invokes option accessors before the native method", async () => {
  document.body.innerHTML =
    '<div class="workspace-scroll"><div></div><div class="workspace-panel"><details><summary>Fixture</summary></details></div></div>';
  const outer = document.querySelector(".workspace-scroll");
  let reads = 0;
  outer.scrollTo = (options) => options.top;
  const options = {
    get top() {
      reads += 1;
      return 100;
    },
  };
  await watchDisclosure(summaryLocator(), "reply-activity");
  assert.equal(outer.scrollTo(options), 100);
  assert.equal(reads, 1);
  const [{ records }] = await disclosureDiagnostics(page);
  assert.equal(records.find((record) => record.kind === "scroll-request").lastScrollRequest.top, null);
});
