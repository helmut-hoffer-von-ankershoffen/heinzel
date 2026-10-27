import assert from "node:assert/strict";
import { generateKeyPairSync, sign } from "node:crypto";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { verifySignedCatalog } from "../signature.js";
import { selectArtifact } from "../catalog.js";

const catalog = JSON.parse(await readFile(new URL("./fixtures/catalog.json", import.meta.url), "utf8"));
const { publicKey, privateKey } = generateKeyPairSync("ed25519");
const rawPublicKey = publicKey.export({ format: "der", type: "spki" }).subarray(-32).toString("base64url");
const trustedKeys = new Map([["test-key-1", rawPublicKey]]);

function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

function signed(overrides = {}) {
  const envelope = { schema_version: 1, key_id: "test-key-1", catalog, ...overrides };
  const bytes = Buffer.from(canonical(envelope));
  return { envelope, bytes, signature: sign(null, bytes, privateKey).toString("base64url") };
}

test("verifies a canonical Ed25519 envelope using only caller-supplied trust roots", () => {
  const value = signed();
  const result = verifySignedCatalog(value.bytes, value.signature, trustedKeys);
  assert.equal(result.key_id, "test-key-1");
  assert.deepEqual(result.catalog, catalog);
});

test("rejects unknown key IDs and malformed trusted key/signature encodings", () => {
  const value = signed({ key_id: "other-key" });
  assert.throws(() => verifySignedCatalog(value.bytes, value.signature, trustedKeys), /not trusted/);
  const valid = signed();
  assert.throws(() => verifySignedCatalog(valid.bytes, valid.signature, new Map([["test-key-1", "***"]])), /base64url/);
  assert.throws(() => verifySignedCatalog(valid.bytes, "abc=", trustedKeys), /unpadded base64url/);
  assert.throws(() => verifySignedCatalog(valid.bytes, "AA", trustedKeys), /encoding or length/);
});

test("rejects malformed, noncanonical, unsupported, and schema-mismatched envelopes", () => {
  const valid = signed();
  assert.throws(() => verifySignedCatalog(Buffer.from("{"), valid.signature, trustedKeys), /valid JSON/);
  assert.throws(() => verifySignedCatalog(Buffer.from(` ${valid.bytes}`), valid.signature, trustedKeys), /not canonical/);
  assert.throws(() => verifySignedCatalog(Buffer.from(valid.bytes.toString().replace('"schema_version":1', '"schema_version":2')), valid.signature, trustedKeys), /schema_version/);
  assert.throws(() => verifySignedCatalog(Buffer.from(valid.bytes.toString().replace('"key_id":', '"extra":true,"key_id":')), valid.signature, trustedKeys), /unsupported fields/);
  assert.throws(() => verifySignedCatalog(Buffer.from(valid.bytes.toString().replace('"key_id":"test-key-1"', '"key_id":"test-key-1","key_id":"test-key-1"')), valid.signature, trustedKeys), /not canonical/);
});

test("rejects invalid signatures and tampered catalog bytes", () => {
  const valid = signed();
  const other = generateKeyPairSync("ed25519");
  const wrongSignature = sign(null, valid.bytes, other.privateKey).toString("base64url");
  assert.throws(() => verifySignedCatalog(valid.bytes, wrongSignature, trustedKeys), /signature verification failed/);
  const tampered = Buffer.from(valid.bytes.toString().replace("0.7.24", "0.7.25"));
  assert.throws(() => verifySignedCatalog(tampered, valid.signature, trustedKeys), /signature verification failed/);
});

test("verified artifact identity and metadata cannot be replaced before selection", () => {
  const value = signed();
  const verified = verifySignedCatalog(value.bytes, value.signature, trustedKeys);
  const artifact = verified.catalog.artifacts[0];
  const original = structuredClone(artifact);
  for (const [field, replacement] of [
    ["url", "https://example.invalid/replaced-after-verification.zip"],
    ["platform", "linux"],
    ["architecture", "x64"],
    ["app_version", "0.7.25"],
    ["core_version", "0.7.25"],
    ["sha256", "c".repeat(64)],
  ]) {
    assert.throws(() => { artifact[field] = replacement; }, TypeError, field);
  }
  assert.throws(() => { artifact.publisher = "replacement"; }, TypeError);
  assert.throws(() => Object.setPrototypeOf(artifact, { url: "https://example.invalid/prototype.zip" }), TypeError);
  assert.deepEqual(artifact, original);
  const request = Object.fromEntries(["platform", "architecture", "app_version", "core_version"].map((field) => [field, original[field]]));
  assert.deepEqual(selectArtifact(verified.catalog, request), original);
});

test("verified catalog metadata and nested collections cannot replace authenticated artifacts", () => {
  const value = signed();
  const verified = verifySignedCatalog(value.bytes, value.signature, trustedKeys);
  assert.throws(() => { verified.catalog.schema_version = 2; }, TypeError);
  assert.throws(() => { verified.catalog.artifacts = []; }, TypeError);
  assert.throws(() => { verified.catalog.artifacts[0] = { ...catalog.artifacts[1] }; }, TypeError);
  assert.throws(() => verified.catalog.artifacts.push({ ...catalog.artifacts[0] }), TypeError);
  assert.throws(() => verified.catalog.artifacts.splice(0, 1), TypeError);
  assert.throws(() => { verified.catalog.extra = "replacement"; }, TypeError);
  assert.deepEqual(verified.catalog, catalog);
  assert.ok(Object.isFrozen(verified));
  assert.ok(Object.isFrozen(verified.catalog));
  assert.ok(Object.isFrozen(verified.catalog.artifacts));
  assert.ok(verified.catalog.artifacts.every(Object.isFrozen));
});
