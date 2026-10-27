import assert from "node:assert/strict";
import { chmodSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { openProfile, parseLaunch } from "../src/profile.js";

test("one stable private identity survives reopen and channels/profiles remain separate", () => {
  const root = mkdtempSync(join(tmpdir(), "hz-profile-"));
  try {
    const first = openProfile(root, { name: "default", channel: "stable" });
    const second = openProfile(root, { name: "default", channel: "stable" });
    assert.deepEqual(first, second);
    assert.notEqual(first.id, openProfile(root, { name: "other", channel: "stable" }).id);
    assert.notEqual(first.id, openProfile(root, { name: "default", channel: "preview" }).id);
    assert.equal(readFileSync(join(first.directory, "profile.json"), "utf8").includes(first.id), true);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("a profile never follows linked identity files or accepts foreign/changed identity metadata", () => {
  const root = mkdtempSync(join(tmpdir(), "hz-profile-"));
  try {
    const profile = openProfile(root, { name: "default", channel: "stable" });
    const file = join(profile.directory, "profile.json");
    const raw = readFileSync(file, "utf8");
    const outside = join(root, "outside.json");
    writeFileSync(outside, raw);
    rmSync(file);
    symlinkSync(outside, file);
    assert.throws(() => openProfile(root, { name: "default", channel: "stable" }));
    rmSync(file);
    writeFileSync(file, raw, { mode: 0o600 });
    chmodSync(file, 0o644);
    assert.throws(() => openProfile(root, { name: "default", channel: "stable" }));
    chmodSync(file, 0o600);
    writeFileSync(file, JSON.stringify({ ...JSON.parse(raw), channel: "preview" }));
    assert.throws(() => openProfile(root, { name: "default", channel: "stable" }));
    writeFileSync(file, JSON.stringify({ ...JSON.parse(raw), unknown: true }));
    assert.throws(() => openProfile(root, { name: "default", channel: "stable" }));
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("launch choices are explicit bounded identifiers and never URLs or filesystem paths", () => {
  assert.deepEqual(parseLaunch([]), { name: "default", channel: "stable", newWindow: false });
  assert.deepEqual(parseLaunch(["--profile=work", "--channel=preview", "--new-window"]), {
    name: "work",
    channel: "preview",
    newWindow: true,
  });
  for (const args of [
    ["--profile=../other"],
    ["--profile=/tmp/x"],
    ["--channel=other"],
    ["--url=http://localhost:8737"],
    ["--profile=a", "--profile=b"],
  ]) {
    assert.throws(() => parseLaunch(args));
  }
});

test("canonical profile names and stored identities reject terminal control characters", () => {
  const root = mkdtempSync(join(tmpdir(), "hz-profile-"));
  try {
    assert.throws(() => parseLaunch(["--profile=work\n"]));
    assert.throws(() => openProfile(root, { name: "work\n", channel: "stable" }));
    const profile = openProfile(root, { name: "work", channel: "stable" });
    const path = join(profile.directory, "profile.json");
    const identity = JSON.parse(readFileSync(path, "utf8"));
    writeFileSync(path, JSON.stringify({ ...identity, id: `${identity.id}\n` }));
    assert.throws(() => openProfile(root, { name: "work", channel: "stable" }));
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
