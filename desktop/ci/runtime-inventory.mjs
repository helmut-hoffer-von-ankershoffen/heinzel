import { constants } from "node:fs";
import { createHash } from "node:crypto";
import { lstat, open, readdir } from "node:fs/promises";
import path from "node:path";
import { extractFile, getRawHeader, listPackage, statFile, uncacheAll } from "@electron/asar";

// Adding a runtime file is an explicit packaging decision, not an automatic inclusion of arbitrary source files.
export const RUNTIME_FILES = Object.freeze([
  "connection.js", "identity.js", "instance-lifecycle.js", "lifecycle-ipc.js", "main.js",
  "native-permissions.js", "owned-cli.js", "preload.cjs", "shutdown.js", "signal-quit.js", "ui-navigation.js",
]);
const MAX_FILE = 2 * 1024 * 1024;

async function regularBytes(file) {
  const handle = await open(file, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
  try {
    const before = await handle.stat();
    if (!before.isFile() || before.size > MAX_FILE) throw new Error(`Unsafe package source: ${file}`);
    const bytes = await handle.readFile();
    const after = await handle.stat();
    if (bytes.length !== before.size || before.size !== after.size || before.mtimeMs !== after.mtimeMs ||
        before.ctimeMs !== after.ctimeMs || before.ino !== after.ino)
      throw new Error(`Package source changed during inspection: ${file}`);
    return bytes;
  } finally { await handle.close(); }
}

/** Capture an exact closed source inventory and the package metadata generated from core's version. */
export async function captureRuntime(packageRoot) {
  const source = path.join(packageRoot, "src");
  const info = await lstat(source);
  if (!info.isDirectory() || info.isSymbolicLink()) throw new Error("Runtime source must be a real directory.");
  const names = (await readdir(source)).sort();
  const expected = [...RUNTIME_FILES].sort();
  if (JSON.stringify(names) !== JSON.stringify(expected)) throw new Error("Runtime source inventory differs from the approved list.");
  const metadataText = (await regularBytes(path.join(packageRoot, "package.json"))).toString("utf8");
  uniqueJson(metadataText);
  const metadata = JSON.parse(metadataText);
  const pyproject = (await regularBytes(path.join(packageRoot, "..", "pyproject.toml"))).toString("utf8");
  const version = pyproject.match(/^version\s*=\s*"([^\"]+)"/m)?.[1];
  if (!version || metadata.main !== "src/main.js") throw new Error("Invalid package metadata or core version.");
  const files = new Map([["package.json", Buffer.from(`${JSON.stringify({ ...metadata, version }, null, 2)}\n`)]]);
  for (const name of expected) files.set(`src/${name}`, await regularBytes(path.join(source, name)));
  const after = await lstat(source);
  if (!after.isDirectory() || after.isSymbolicLink() || after.dev !== info.dev || after.ino !== info.ino)
    throw new Error("Runtime source directory changed during inspection.");
  return { version, electronVersion: metadata.devDependencies?.electron, files };
}

/** Reject duplicate JSON keys at every nesting level before trusting an ASAR header. */
function uniqueJson(text) {
  let pos = 0;
  const white = () => { while (/\s/.test(text[pos] ?? "") && pos < text.length) pos++; };
  function string() {
    const start = pos++;
    while (pos < text.length) {
      const char = text[pos++];
      if (char === "\\") { pos++; continue; }
      if (char === '"') return JSON.parse(text.slice(start, pos));
    }
    throw new Error("Invalid ASAR header string.");
  }
  function value(depth) {
    if (depth > 128) throw new Error("ASAR header is too deeply nested.");
    white();
    if (text[pos] === '"') { string(); return; }
    if (text[pos] === "{") {
      pos++; white(); const keys = new Set();
      if (text[pos] === "}") { pos++; return; }
      while (true) {
        white(); if (text[pos] !== '"') throw new Error("Invalid ASAR header object.");
        const key = string();
        if (keys.has(key)) throw new Error("Duplicate ASAR header entry.");
        keys.add(key); white(); if (text[pos++] !== ":") throw new Error("Invalid ASAR header object.");
        value(depth + 1); white();
        if (text[pos] === "}") { pos++; return; }
        if (text[pos++] !== ",") throw new Error("Invalid ASAR header object.");
      }
    }
    if (text[pos] === "[") {
      pos++; white(); if (text[pos] === "]") { pos++; return; }
      while (true) {
        value(depth + 1); white();
        if (text[pos] === "]") { pos++; return; }
        if (text[pos++] !== ",") throw new Error("Invalid ASAR header array.");
      }
    }
    const found = /^(?:true|false|null|-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?)/.exec(text.slice(pos));
    if (!found) throw new Error("Invalid ASAR header value.");
    pos += found[0].length;
  }
  value(0); white(); if (pos !== text.length) throw new Error("Invalid ASAR header trailing data.");
}

/** Verify all archive entries and bytes; this is continuity evidence, not publisher or signature proof. */
export async function verifyRuntime(archivePath, packageRoot) {
  const expected = await captureRuntime(packageRoot);
  const archive = await open(archivePath, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
  let archiveInfo;
  try {
    archiveInfo = await archive.stat();
    const prefix = Buffer.alloc(8);
    const { bytesRead } = await archive.read(prefix, 0, 8, 0);
    const headerSize = prefix.readUInt32LE(4);
    if (!archiveInfo.isFile() || bytesRead !== 8 || headerSize > 1024 * 1024 || headerSize > archiveInfo.size - 8 ||
        archiveInfo.size > (RUNTIME_FILES.length + 1) * MAX_FILE + 1024 * 1024 + 8)
      throw new Error("Unsafe or oversized ASAR archive.");
  } finally { await archive.close(); }
  uncacheAll();
  const header = getRawHeader(archivePath);
  uniqueJson(header.headerString);
  if (Object.keys(header.header).length !== 1 || !("files" in header.header))
    throw new Error("Unexpected ASAR root metadata.");
  const entries = listPackage(archivePath).map((name) => name.replace(/^\//, ""));
  const wanted = ["src", ...expected.files.keys()].sort();
  if (new Set(entries).size !== entries.length || JSON.stringify([...entries].sort()) !== JSON.stringify(wanted))
    throw new Error("Packaged runtime inventory differs from source.");
  const directory = statFile(archivePath, "src", false);
  if (!("files" in directory) || Object.keys(directory).length !== 1) throw new Error("Unsafe packaged source directory.");
  const ranges = [];
  for (const [name, bytes] of expected.files) {
    const info = statFile(archivePath, name, false);
    if ("link" in info || "files" in info || info.unpacked || typeof info.offset !== "string" || !/^(0|[1-9][0-9]*)$/.test(info.offset) ||
        !Number.isSafeInteger(Number(info.offset)) || info.size !== bytes.length || !extractFile(archivePath, name, false).equals(bytes))
      throw new Error(`Packaged runtime differs from source: ${name}`);
    const hash = createHash("sha256").update(bytes).digest("hex");
    const integrity = info.integrity;
    if (JSON.stringify(Object.keys(info).sort()) !== JSON.stringify(["integrity", "offset", "size"]) ||
        !integrity || JSON.stringify(Object.keys(integrity).sort()) !== JSON.stringify(["algorithm", "blockSize", "blocks", "hash"]) ||
        integrity.algorithm !== "SHA256" || integrity.blockSize !== 4 * 1024 * 1024 || integrity.hash !== hash ||
        !Array.isArray(integrity.blocks) || integrity.blocks.length !== 1 || integrity.blocks[0] !== hash)
      throw new Error(`Invalid ASAR integrity metadata: ${name}`);
    ranges.push({ offset: Number(info.offset), size: info.size });
  }
  ranges.sort((a, b) => a.offset - b.offset);
  let end = 0;
  for (const range of ranges) {
    if (range.offset !== end) throw new Error("ASAR runtime bytes overlap or contain unexpected gaps.");
    end += range.size;
  }
  if (8 + header.headerSize + end !== archiveInfo.size) throw new Error("ASAR contains unexpected trailing bytes.");
  const after = await lstat(archivePath);
  if (!after.isFile() || after.isSymbolicLink() || after.dev !== archiveInfo.dev || after.ino !== archiveInfo.ino ||
      after.size !== archiveInfo.size || after.mtimeMs !== archiveInfo.mtimeMs || after.ctimeMs !== archiveInfo.ctimeMs)
    throw new Error("ASAR archive changed during verification.");
  return expected.version;
}
