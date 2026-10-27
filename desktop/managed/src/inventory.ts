import { createHash } from "node:crypto";
import { closeSync, constants, fstatSync, lstatSync, mkdirSync, openSync, readdirSync, readFileSync } from "node:fs";
import { basename, dirname, join, resolve } from "node:path";

const DIGEST = /^[a-f0-9]{64}$/;
const VERSION = /^\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?$/;
const FILE = /^(?!.*(?:^|\/)\.\.?\/)[a-zA-Z0-9_+.-]+(?:\/[a-zA-Z0-9_+.-]+)*$/;
const MAX_METADATA = 8 * 1024 * 1024;

export class DeploymentError extends Error {
  constructor() {
    super("The owned runtime inventory is unavailable or changed. Repair this profile before starting it.");
  }
}

export interface RuntimeManifest {
  readonly schema: 1;
  readonly core_version: string;
  readonly python: string;
  readonly python_sha256: string;
  readonly ui_sha256: string;
  readonly files: Readonly<Record<string, string>>;
}
export interface Deployment {
  readonly id: string;
  readonly directory: string;
  readonly manifest: RuntimeManifest;
}
export interface Activation {
  readonly schema: 1;
  readonly generation: number;
  readonly deployment: string;
  readonly previous: string | null;
}

export interface BackendInventory {
  active(): Activation | null;
  verify(id: string): Deployment;
}

const digest = (bytes: Uint8Array) => createHash("sha256").update(bytes).digest("hex");
const keys = (value: Record<string, unknown>, expected: string) => Object.keys(value).sort().join(",") === expected;
const record = (value: unknown): value is Record<string, unknown> =>
  !!value && typeof value === "object" && !Array.isArray(value);

function privateDirectory(path: string): void {
  mkdirSync(path, { recursive: true, mode: 0o700 });
  checkDirectory(path);
}

function checkDirectory(path: string): void {
  const stat = lstatSync(path);
  if (
    !process.getuid ||
    !stat.isDirectory() ||
    stat.isSymbolicLink() ||
    stat.uid !== process.getuid() ||
    stat.mode & 0o077
  )
    throw new DeploymentError();
}

function readRegular(path: string, limit = MAX_METADATA, executable = false): Buffer {
  const descriptor = openSync(path, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
  try {
    const stat = fstatSync(descriptor);
    if (
      !stat.isFile() ||
      stat.nlink !== 1 ||
      stat.size > limit ||
      (executable ? stat.uid !== 0 && stat.uid !== process.getuid?.() : stat.uid !== process.getuid?.()) ||
      stat.mode & (executable ? 0o022 : 0o077)
    )
      throw new DeploymentError();
    return readFileSync(descriptor);
  } finally {
    closeSync(descriptor);
  }
}

function readJson(path: string): unknown {
  return JSON.parse(readRegular(path).toString("utf8"));
}

function installedPath(value: string): boolean {
  return !/[\\\p{C}]/u.test(value) && value.split("/").every((part) => part !== "" && part !== "." && part !== "..");
}

function verifyInventory(directory: string, files: Record<string, unknown>, skip?: string, environment = false): void {
  const listed = new Set(Object.keys(files));
  const visit = (relative: string) => {
    const current = join(directory, relative);
    checkDirectory(current);
    for (const entry of readdirSync(current, { withFileTypes: true })) {
      const name = relative ? `${relative}/${entry.name}` : entry.name;
      if (name === skip) continue;
      if (!(environment ? installedPath(name) : FILE.test(name)) || entry.isSymbolicLink()) throw new DeploymentError();
      if (entry.isDirectory()) visit(name);
      else if (!entry.isFile() || !listed.delete(name)) throw new DeploymentError();
      else if (
        typeof files[name] !== "string" ||
        digest(readRegular(join(directory, name), 256 * 1024 * 1024)) !== files[name]
      )
        throw new DeploymentError();
    }
  };
  visit("");
  if (listed.size) throw new DeploymentError();
}

function verifyEnvironment(root: string, directory: string, python: string): void {
  const data = readJson(join(directory, "environment.json"));
  if (
    !record(data) ||
    !keys(data, "directory,files,requested,schema,versions,wheel_sha256") ||
    data.schema !== 1 ||
    typeof data.directory !== "string" ||
    resolve(data.directory) !== data.directory ||
    dirname(data.directory) !== join(root, "environments") ||
    !/^[a-f0-9]{32}$/.test(basename(data.directory)) ||
    python !== join(data.directory, "bin/python") ||
    !record(data.files) ||
    !Object.keys(data.files).length ||
    !record(data.versions) ||
    !record(data.requested) ||
    !record(data.wheel_sha256) ||
    Object.values(data.wheel_sha256).some((value) => typeof value !== "string" || !DIGEST.test(value))
  )
    throw new DeploymentError();
  checkDirectory(join(root, "environments"));
  verifyInventory(data.directory, data.files, undefined, true);
}

function activation(value: unknown): Activation {
  if (
    !record(value) ||
    !keys(value, "deployment,generation,previous,schema") ||
    value.schema !== 1 ||
    !Number.isSafeInteger(value.generation) ||
    Number(value.generation) < 1 ||
    typeof value.deployment !== "string" ||
    !DIGEST.test(value.deployment) ||
    (value.previous !== null && (typeof value.previous !== "string" || !DIGEST.test(value.previous)))
  )
    throw new DeploymentError();
  return value as unknown as Activation;
}

/** Native reads cannot change activation or journals; only the existing shared Python authority writes them. */
export class RuntimeInventory implements BackendInventory {
  readonly directory: string;

  constructor(root: string) {
    this.directory = resolve(root);
    privateDirectory(this.directory);
    privateDirectory(join(this.directory, "deployments"));
  }

  active(): Activation | null {
    try {
      checkDirectory(this.directory);
      return activation(readJson(join(this.directory, "active.json")));
    } catch (error) {
      if (record(error) && error.code === "ENOENT") return null;
      throw new DeploymentError();
    }
  }

  verify(id: string): Deployment {
    try {
      checkDirectory(this.directory);
      checkDirectory(join(this.directory, "deployments"));
      if (!DIGEST.test(id)) throw new DeploymentError();
      const directory = join(this.directory, "deployments", id);
      checkDirectory(directory);
      const manifestBytes = readRegular(join(directory, "runtime.json"));
      if (digest(manifestBytes) !== id) throw new DeploymentError();
      const data: unknown = JSON.parse(manifestBytes.toString("utf8"));
      if (
        !record(data) ||
        !keys(data, "core_version,files,python,python_sha256,schema,ui_sha256") ||
        data.schema !== 1 ||
        typeof data.core_version !== "string" ||
        !VERSION.test(data.core_version) ||
        typeof data.python !== "string" ||
        !data.python.startsWith("/") ||
        typeof data.python_sha256 !== "string" ||
        !DIGEST.test(data.python_sha256) ||
        typeof data.ui_sha256 !== "string" ||
        !DIGEST.test(data.ui_sha256) ||
        !record(data.files) ||
        !Object.keys(data.files).length
      )
        throw new DeploymentError();
      if (digest(readRegular(data.python, 256 * 1024 * 1024, true)) !== data.python_sha256) throw new DeploymentError();
      verifyInventory(directory, data.files, "runtime.json");
      if (data.files["environment.json"]) verifyEnvironment(this.directory, directory, data.python);
      return Object.freeze({ id, directory, manifest: data as unknown as RuntimeManifest });
    } catch {
      throw new DeploymentError();
    }
  }
}
