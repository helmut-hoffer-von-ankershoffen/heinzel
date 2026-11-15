import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { createSettingsStore } from "../src/settings-store.js";

const folder = () => path.join(mkdtempSync(path.join(tmpdir(), "hz-settings-")), "data", "settings.json");

test("links open in the built-in browser by default, and the choice survives a restart", () => {
  const file = folder();
  const first = createSettingsStore({ file });
  assert.equal(first.get("linksInBrowser"), true);
  assert.equal(first.set("linksInBrowser", false), true);
  assert.equal(first.get("linksInBrowser"), false);
  assert.equal(JSON.parse(readFileSync(file, "utf8")).linksInBrowser, false);
  assert.equal(createSettingsStore({ file }).get("linksInBrowser"), false);
  first.set("linksInBrowser", true);
  assert.equal(createSettingsStore({ file }).get("linksInBrowser"), true);
});

test("a malformed file, a wrong type or an unknown key never changes the defaults", () => {
  const file = folder();
  const store = createSettingsStore({ file });
  store.set("linksInBrowser", true);
  for (const content of ["not json", "[]", "null", '{"linksInBrowser":"no"}', '{"other":1}'])
    { writeFileSync(file, content); assert.equal(createSettingsStore({ file }).get("linksInBrowser"), true, content); }
  assert.throws(() => store.set("linksInBrowser", "no"));
  assert.throws(() => store.set("other", true));
  assert.equal(store.get("other"), undefined);
});

test("a write that fails keeps the choice for this run and says it was not saved", () => {
  const store = createSettingsStore({ file: "/nonexistent/x.json", mkdir: () => { throw new Error("read-only"); } });
  assert.equal(store.set("linksInBrowser", false), false);
  assert.equal(store.get("linksInBrowser"), false);
});
