import { createPublicKey, verify } from "node:crypto";
import { validateCatalog } from "./catalog.js";

const ENVELOPE_FIELDS = new Set(["schema_version", "key_id", "catalog"]);
const KEY_ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;
const BASE64URL = /^[A-Za-z0-9_-]+$/;
const ED25519_SPKI_PREFIX = Buffer.from("302a300506032b6570032100", "hex");

function reject(message) {
  throw new TypeError(`Invalid signed desktop catalog: ${message}`);
}

function sortedJson(value) {
  if (Array.isArray(value)) return `[${value.map(sortedJson).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${sortedJson(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

function decodeCanonicalBase64url(value, label, expectedBytes) {
  if (typeof value !== "string" || !BASE64URL.test(value)) reject(`${label} must be unpadded base64url`);
  const bytes = Buffer.from(value, "base64url");
  if (bytes.length !== expectedBytes || bytes.toString("base64url") !== value) reject(`${label} has an invalid encoding or length`);
  return bytes;
}

function keyFor(keyId, trustedKeys) {
  const encoded = trustedKeys instanceof Map
    ? trustedKeys.get(keyId)
    : trustedKeys && Object.hasOwn(trustedKeys, keyId) ? trustedKeys[keyId] : undefined;
  if (encoded === undefined) reject("key_id is not trusted");
  const raw = decodeCanonicalBase64url(encoded, "trusted Ed25519 public key", 32);
  return createPublicKey({ key: Buffer.concat([ED25519_SPKI_PREFIX, raw]), format: "der", type: "spki" });
}

/**
 * Verify a detached Ed25519 signature over a canonical schema-v1 catalog envelope.
 * The caller supplies the trust map; this function has no embedded keys or network behavior.
 */
export function verifySignedCatalog(envelopeBytes, signatureBase64url, trustedKeys) {
  let bytes;
  try {
    bytes = Buffer.isBuffer(envelopeBytes) ? envelopeBytes : Buffer.from(envelopeBytes, "utf8");
  } catch {
    reject("envelope must be UTF-8 text or bytes");
  }
  const source = bytes.toString("utf8");
  if (!Buffer.from(source, "utf8").equals(bytes)) reject("envelope must be valid UTF-8");

  let envelope;
  try {
    envelope = JSON.parse(source);
  } catch {
    reject("envelope must be valid JSON");
  }
  if (!envelope || typeof envelope !== "object" || Array.isArray(envelope)) reject("envelope must be an object");
  const keys = Object.keys(envelope);
  if (keys.length !== ENVELOPE_FIELDS.size || keys.some((key) => !ENVELOPE_FIELDS.has(key))) {
    reject("envelope has missing or unsupported fields");
  }
  if (envelope.schema_version !== 1) reject("schema_version must be 1");
  if (typeof envelope.key_id !== "string" || !KEY_ID.test(envelope.key_id)) reject("key_id is malformed");
  validateCatalog(envelope.catalog);
  const canonical = sortedJson({ schema_version: envelope.schema_version, key_id: envelope.key_id, catalog: envelope.catalog });
  if (source !== canonical) reject("envelope JSON is not canonical");

  const signature = decodeCanonicalBase64url(signatureBase64url, "signature", 64);
  const key = keyFor(envelope.key_id, trustedKeys);
  if (!verify(null, Buffer.from(canonical, "utf8"), key, signature)) reject("signature verification failed");
  // Schema v1 contains only these containers and primitive artifact fields; preserve the authenticated snapshot.
  for (const artifact of envelope.catalog.artifacts) Object.freeze(artifact);
  Object.freeze(envelope.catalog.artifacts);
  Object.freeze(envelope.catalog);
  return Object.freeze({ key_id: envelope.key_id, catalog: envelope.catalog });
}
