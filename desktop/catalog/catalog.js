const VERSION = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-((?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*))?$/;
const DIGEST = /^[a-f0-9]{64}$/;
const PLATFORMS = new Set(["darwin", "linux", "win32"]);
const ARCHITECTURES = new Set(["arm64", "x64"]);
const CATALOG_FIELDS = new Set(["schema_version", "artifacts"]);
const ARTIFACT_FIELDS = new Set(["platform", "architecture", "app_version", "core_version", "url", "sha256"]);

function fail(message) {
  throw new TypeError(`Invalid desktop release catalog: ${message}`);
}

function exactFields(value, expected, label) {
  if (!value || typeof value !== "object" || Array.isArray(value)) fail(`${label} must be an object`);
  const keys = Object.keys(value);
  if (keys.length !== expected.size || keys.some((key) => !expected.has(key))) fail(`${label} has missing or unsupported fields`);
}

function canonicalVersion(value, label) {
  if (typeof value !== "string" || !VERSION.test(value)) fail(`${label} must be a canonical semantic version`);
}

function stableHttpsUrl(value) {
  if (typeof value !== "string") return false;
  let url;
  try {
    url = new URL(value);
  } catch {
    return false;
  }
  return url.protocol === "https:" && url.hostname !== "" && !url.username && !url.password &&
    !url.port && !value.includes("?") && !value.includes("#") && url.href === value && !value.endsWith("/");
}

/** Validate a schema-v1 catalog. Validation is pure and does not fetch or install anything. */
export function validateCatalog(catalog) {
  exactFields(catalog, CATALOG_FIELDS, "catalog");
  if (catalog.schema_version !== 1) fail("schema_version must be 1");
  if (!Array.isArray(catalog.artifacts) || catalog.artifacts.length === 0) fail("artifacts must be a non-empty array");

  const identities = new Set();
  const urls = new Set();
  for (const [index, artifact] of catalog.artifacts.entries()) {
    const label = `artifacts[${index}]`;
    exactFields(artifact, ARTIFACT_FIELDS, label);
    if (!PLATFORMS.has(artifact.platform)) fail(`${label}.platform is unsupported`);
    if (!ARCHITECTURES.has(artifact.architecture)) fail(`${label}.architecture is unsupported`);
    canonicalVersion(artifact.app_version, `${label}.app_version`);
    canonicalVersion(artifact.core_version, `${label}.core_version`);
    if (!stableHttpsUrl(artifact.url)) fail(`${label}.url must be a stable canonical HTTPS URL`);
    if (typeof artifact.sha256 !== "string" || !DIGEST.test(artifact.sha256)) fail(`${label}.sha256 must be lowercase SHA-256 hex`);

    const identity = [artifact.platform, artifact.architecture, artifact.app_version, artifact.core_version].join("\0");
    if (identities.has(identity)) fail(`${label} duplicates an artifact compatibility tuple`);
    if (urls.has(artifact.url)) fail(`${label}.url duplicates another artifact URL`);
    identities.add(identity);
    urls.add(artifact.url);
  }
  return catalog;
}

/** Select the unique artifact compatible with the requested platform, architecture and exact versions. */
export function selectArtifact(catalog, request) {
  validateCatalog(catalog);
  exactFields(request, new Set(["platform", "architecture", "app_version", "core_version"]), "request");
  if (!PLATFORMS.has(request.platform)) fail("request.platform is unsupported");
  if (!ARCHITECTURES.has(request.architecture)) fail("request.architecture is unsupported");
  canonicalVersion(request.app_version, "request.app_version");
  canonicalVersion(request.core_version, "request.core_version");
  const matches = catalog.artifacts.filter((item) =>
    item.platform === request.platform && item.architecture === request.architecture &&
    item.app_version === request.app_version && item.core_version === request.core_version);
  if (matches.length !== 1) fail(matches.length ? "artifact selection is ambiguous" : "no compatible artifact exists");
  return Object.freeze({ ...matches[0] });
}
