import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, rename, rm, symlink, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { createPackage, getRawHeader, uncacheAll } from "@electron/asar";
import { captureRuntime, RUNTIME_FILES, verifyRuntime } from "../ci/runtime-inventory.mjs";

async function setup(t) {
  const root = await mkdtemp(path.join(os.tmpdir(), "hz-asar-inventory-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const project = path.join(root, "desktop"), src = path.join(project, "src");
  await mkdir(src, { recursive: true });
  await writeFile(path.join(root, "pyproject.toml"), '[project]\nversion = "1.2.3"\n');
  await writeFile(path.join(project, "package.json"), JSON.stringify({ main: "src/main.js", devDependencies: { electron: "45.0.0" } }));
  for (const name of RUNTIME_FILES) await writeFile(path.join(src, name), `// trusted ${name}\n`);
  const runtime = await captureRuntime(project);
  const stage = path.join(root, "staging");
  await mkdir(path.join(stage, "src"), { recursive: true });
  for (const [name, bytes] of runtime.files) await writeFile(path.join(stage, name), bytes);
  return { root, project, stage };
}

async function archive(root, stage, label) {
  const target = path.join(root, `${label}.asar`);
  await createPackage(stage, target);
  return target;
}

test("accepts only the exact source and generated package metadata", async (t) => {
  const { root, project, stage } = await setup(t);
  const file = await archive(root, stage, "valid");
  assert.equal(await verifyRuntime(file, project), "1.2.3");
  await writeFile(path.join(stage, "package.json"), '{"main":"src/main.js","version":"1.2.3"}\n');
  await assert.rejects(verifyRuntime(await archive(root, stage, "metadata"), project), /differs from source/);
});

test("rejects changed, missing and unexpected runtime files", async (t) => {
  const { root, project, stage } = await setup(t);
  const main = path.join(stage, "src", "main.js");
  const original = await readFile(main);
  await writeFile(main, "// changed\n");
  await assert.rejects(verifyRuntime(await archive(root, stage, "changed"), project), /differs from source/);
  await writeFile(main, original);
  await writeFile(path.join(stage, "src", "unexpected.js"), "// extra\n");
  await assert.rejects(verifyRuntime(await archive(root, stage, "extra"), project), /inventory/);
  await rm(path.join(stage, "src", "unexpected.js"));
  await rm(path.join(stage, "src", "connection.js"));
  await assert.rejects(verifyRuntime(await archive(root, stage, "missing"), project), /inventory/);
});

test("rejects symlinked source and archive links", async (t) => {
  const { root, project, stage } = await setup(t);
  const source = path.join(project, "src", "main.js");
  await rm(source); await symlink(path.join(project, "src", "connection.js"), source);
  await assert.rejects(captureRuntime(project));
  await rm(source); await writeFile(source, "// trusted main.js\n");
  const staged = path.join(stage, "src", "main.js");
  await rm(staged); await symlink(path.join(stage, "src", "connection.js"), staged);
  await assert.rejects(verifyRuntime(await archive(root, stage, "link"), project));
});

test("rejects duplicate JSON keys in the actual ASAR header", async (t) => {
  const { root, project, stage } = await setup(t);
  const file = await archive(root, stage, "duplicate");
  const header = getRawHeader(file).headerString;
  const match = /"offset":"(\d+)"/.exec(header);
  assert(match);
  // Reuse the exact byte count, so this is a real archive with a duplicate JSON key.
  const replacement = `"size":${" ".repeat(match[0].length - 8)}0`;
  assert.equal(replacement.length, match[0].length);
  const modified = header.replace(match[0], replacement);
  const bytes = await readFile(file);
  const at = bytes.indexOf(Buffer.from(header));
  assert.ok(at >= 0);
  Buffer.from(modified).copy(bytes, at);
  await writeFile(file, bytes); uncacheAll();
  await assert.rejects(verifyRuntime(file, project), /Duplicate ASAR header entry/);
});

test("rejects unexpected trailing ASAR bytes and symlinked archive", async (t) => {
  const { root, project, stage } = await setup(t);
  const file = await archive(root, stage, "trailing");
  await writeFile(file, Buffer.concat([await readFile(file), Buffer.from("unexpected")]));
  await assert.rejects(verifyRuntime(file, project), /trailing bytes/);
  const valid = await archive(root, stage, "valid-link-target");
  const link = path.join(root, "link.asar");
  await symlink(valid, link);
  await assert.rejects(verifyRuntime(link, project));
});

test("rejects an altered ASAR integrity hash even when file bytes are unchanged", async (t) => {
  const { root, project, stage } = await setup(t);
  const file = await archive(root, stage, "bad-integrity");
  const header = getRawHeader(file).headerString;
  const found = /"hash":"([0-9a-f]{64})"/.exec(header);
  assert(found);
  const replacement = found[1][0] === "0" ? `1${found[1].slice(1)}` : `0${found[1].slice(1)}`;
  const modified = header.replace(found[1], replacement);
  const bytes = await readFile(file), at = bytes.indexOf(Buffer.from(header));
  assert.ok(at >= 0);
  Buffer.from(modified).copy(bytes, at);
  await writeFile(file, bytes); uncacheAll();
  await assert.rejects(verifyRuntime(file, project), /integrity metadata/);
});


test("rejects unexpected source files, linked directories and duplicate package metadata keys", async (t) => {
  const { project } = await setup(t);
  const src = path.join(project, "src");
  const extra = path.join(src, "extra.js");
  await writeFile(extra, "// unexpected\n");
  await assert.rejects(captureRuntime(project), /inventory/);
  await rm(extra);
  const actual = path.join(project, "actual-src");
  await rename(src, actual); await symlink(actual, src);
  await assert.rejects(captureRuntime(project), /real directory/);
  await rm(src); await rename(actual, src);
  await writeFile(path.join(project, "package.json"), '{"main":"src/main.js","main":"src/main.js"}');
  await assert.rejects(captureRuntime(project), /Duplicate/);
});
