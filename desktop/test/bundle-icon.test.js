import assert from "node:assert/strict";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { assertBundleIcon, EXTEND_INFO, iconFileName } from "../ci/bundle-icon.mjs";

const plist = (icon) => `<?xml version="1.0"?><plist><dict><key>CFBundleIconFile</key><string>${icon}</string></dict></plist>`;

async function bundle(t, icon, withFile) {
  const root = await mkdtemp(path.join(os.tmpdir(), "hz-bundle-icon-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const resources = path.join(root, "Contents", "Resources");
  await mkdir(resources, { recursive: true });
  await writeFile(path.join(root, "Contents", "Info.plist"), plist(icon));
  if (withFile) await writeFile(path.join(resources, "heinzel.icns"), "icns");
  return root;
}

test("adds .icns when the plist leaves it off", () => {
  assert.equal(iconFileName(plist("heinzel")), "heinzel.icns");
  assert.equal(iconFileName(plist("heinzel.icns")), "heinzel.icns");
  assert.throws(() => iconFileName("<plist/>"), /no CFBundleIconFile/);
});

test("accepts a bundle whose Resources hold the named icon", async (t) => {
  assert.match(await assertBundleIcon(await bundle(t, "heinzel.icns", true)), /heinzel\.icns$/);
});

test("rejects a bundle whose Resources lack the named icon", async (t) => {
  await assert.rejects(assertBundleIcon(await bundle(t, "heinzel.icns", false)), /missing from Contents\/Resources/);
});

test("shows Heinzelmännchen as the display name", () => {
  assert.equal(EXTEND_INFO.CFBundleDisplayName, "Heinzelmännchen");
});

test("names the visualizer when macOS asks to allow system audio", () => {
  assert.match(EXTEND_INFO.NSAudioCaptureUsageDescription, /audio visualizer/);
  assert.match(EXTEND_INFO.NSAudioCaptureUsageDescription, /Nothing is recorded or uploaded/);
});
