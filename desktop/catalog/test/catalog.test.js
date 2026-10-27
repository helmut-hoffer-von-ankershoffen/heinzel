import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { selectArtifact, validateCatalog } from "../catalog.js";

const fixture = JSON.parse(await readFile(new URL("./fixtures/catalog.json", import.meta.url), "utf8"));
const request = { platform: "linux", architecture: "x64", app_version: "0.7.24", core_version: "0.7.24" };
const clone = (value) => structuredClone(value);

test("validates fixture and selects the exact compatible artifact with its digest and URL", () => {
  assert.equal(validateCatalog(fixture), fixture);
  assert.deepEqual(selectArtifact(fixture, request), fixture.artifacts[1]);
  assert.ok(Object.isFrozen(selectArtifact(fixture, request)));
});

test("rejects unsupported schema and extra or missing fields", () => {
  const wrongSchema = clone(fixture);
  wrongSchema.schema_version = 2;
  assert.throws(() => validateCatalog(wrongSchema), /schema_version/);
  const extraCatalog = { ...clone(fixture), publisher: "unknown" };
  assert.throws(() => validateCatalog(extraCatalog), /unsupported fields/);
  const missing = clone(fixture);
  delete missing.artifacts[0].sha256;
  assert.throws(() => validateCatalog(missing), /unsupported fields/);
  const extraArtifact = clone(fixture);
  extraArtifact.artifacts[0].channel = "stable";
  assert.throws(() => validateCatalog(extraArtifact), /unsupported fields/);
});

test("requires canonical semantic versions and exact requested compatibility", () => {
  const invalid = clone(fixture);
  invalid.artifacts[0].app_version = "v0.7.24";
  assert.throws(() => validateCatalog(invalid), /canonical semantic version/);
  assert.throws(() => selectArtifact(fixture, { ...request, app_version: "0.07.24" }), /canonical semantic version/);
  assert.throws(() => selectArtifact(fixture, { ...request, core_version: "0.7.23" }), /no compatible artifact/);
});

test("requires supported platform/architecture, stable HTTPS URL and lowercase SHA-256", () => {
  for (const [field, value, message] of [
    ["platform", "freebsd", /platform is unsupported/],
    ["architecture", "riscv64", /architecture is unsupported/],
    ["url", "http://example.com/file.zip", /stable canonical HTTPS URL/],
    ["url", "https://example.com/file.zip?latest=1", /stable canonical HTTPS URL/],
    ["url", "https://example.com/file.zip#part", /stable canonical HTTPS URL/],
    ["sha256", "A".repeat(64), /lowercase SHA-256 hex/],
    ["sha256", "0".repeat(63), /lowercase SHA-256 hex/],
  ]) {
    const invalid = clone(fixture);
    invalid.artifacts[0][field] = value;
    assert.throws(() => validateCatalog(invalid), message);
  }
});

test("rejects duplicate compatibility tuples and artifact URLs", () => {
  const duplicateTuple = clone(fixture);
  duplicateTuple.artifacts.push({ ...duplicateTuple.artifacts[0], sha256: "c".repeat(64) });
  assert.throws(() => selectArtifact(duplicateTuple, {
    platform: "darwin", architecture: "arm64", app_version: "0.7.24", core_version: "0.7.24",
  }), /duplicates an artifact compatibility tuple/);
  const duplicateUrl = clone(fixture);
  duplicateUrl.artifacts[1].url = duplicateUrl.artifacts[0].url;
  assert.throws(() => validateCatalog(duplicateUrl), /duplicates another artifact URL/);
});

test("rejects missing and ambiguous selections", () => {
  assert.throws(() => selectArtifact(fixture, { ...request, platform: "darwin" }), /no compatible artifact/);
  const ambiguous = clone(fixture);
  ambiguous.artifacts.push({ ...ambiguous.artifacts[1], architecture: "x64" });
  assert.throws(() => selectArtifact(ambiguous, request), /duplicates an artifact compatibility tuple/);
});

test("selected metadata is an immutable snapshot without an alias to caller-owned catalog data", () => {
  const input = clone(fixture);
  const selected = selectArtifact(input, request);
  const original = clone(selected);
  input.artifacts[1].url = "https://example.invalid/changed-source.tar.gz";
  input.artifacts[1].platform = "darwin";
  input.artifacts[1].sha256 = "c".repeat(64);
  for (const field of Object.keys(selected)) {
    assert.throws(() => { selected[field] = "replacement"; }, TypeError, field);
  }
  assert.throws(() => { selected.publisher = "replacement"; }, TypeError);
  assert.deepEqual(selected, original);
});

for (const delimiter of ["?", "#"]) {
  test(`rejects a URL with an empty ${delimiter} delimiter`, () => {
    const input = clone(fixture);
    input.artifacts[0].url = `https://example.com/file.zip${delimiter}`;
    assert.throws(() => validateCatalog(input), /stable canonical HTTPS URL/);
  });
}

test("allows percent-encoded query and fragment characters that are literal path data", () => {
  const input = clone(fixture);
  input.artifacts[0].url = "https://example.com/file%3F%23.zip";
  assert.equal(validateCatalog(input), input);
});
