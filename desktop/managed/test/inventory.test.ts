import assert from "node:assert/strict";
import {
  chmodSync,
  linkSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  renameSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { DeploymentError, RuntimeInventory } from "../src/inventory.js";
import { digest } from "./fixture.js";

function fixture(overrides: Record<string, unknown> = {}) {
  const root = mkdtempSync(join(tmpdir(), "hz-runtime-inventory-"));
  chmodSync(root, 0o700);
  const inventory = new RuntimeInventory(join(root, "lifecycle"));
  const python = join(root, "python");
  writeFileSync(python, "inventoried interpreter fixture, never executed", { mode: 0o700 });
  const worker = "# inventoried worker\n";
  const manifest = {
    schema: 1,
    core_version: "0.7.12",
    python,
    python_sha256: digest(readFileSync(python)),
    ui_sha256: "a".repeat(64),
    files: { "worker.py": digest(worker) },
    ...overrides,
  };
  const bytes = `${JSON.stringify(manifest)}\n`,
    id = digest(bytes);
  const directory = join(inventory.directory, "deployments", id);
  mkdirSync(directory, { mode: 0o700 });
  writeFileSync(join(directory, "runtime.json"), bytes, { mode: 0o600 });
  writeFileSync(join(directory, "worker.py"), worker, { mode: 0o600 });
  const active = { schema: 1, generation: 1, deployment: id, previous: null };
  writeFileSync(join(inventory.directory, "active.json"), `${JSON.stringify(active)}\n`, { mode: 0o600 });
  return {
    root,
    inventory,
    python,
    directory,
    id,
    active,
    manifest,
    close: () => rmSync(root, { recursive: true, force: true }),
  };
}

function environmentFixture(overrides: Record<string, unknown> = {}) {
  const f = fixture();
  const environments = join(f.inventory.directory, "environments");
  const environment = join(environments, "b".repeat(32));
  mkdirSync(join(environment, "bin"), { recursive: true, mode: 0o700 });
  const python = join(environment, "bin/python");
  const bytes = "owned interpreter fixture, never executed";
  writeFileSync(python, bytes, { mode: 0o700 });
  const metadata = `${JSON.stringify({
    schema: 1,
    directory: environment,
    requested: { heinzel: "0.7.12" },
    versions: { heinzel: "0.7.12" },
    wheel_sha256: { heinzel: "c".repeat(64) },
    files: { "bin/python": digest(bytes) },
    ...overrides,
  })}\n`;
  const manifest = {
    ...f.manifest,
    python,
    python_sha256: digest(bytes),
    files: { ...f.manifest.files, "environment.json": digest(metadata) },
  };
  const manifestBytes = `${JSON.stringify(manifest)}\n`;
  const id = digest(manifestBytes);
  const directory = join(f.inventory.directory, "deployments", id);
  mkdirSync(directory, { mode: 0o700 });
  writeFileSync(join(directory, "runtime.json"), manifestBytes, { mode: 0o600 });
  writeFileSync(join(directory, "worker.py"), readFileSync(join(f.directory, "worker.py")), { mode: 0o600 });
  writeFileSync(join(directory, "environment.json"), metadata, { mode: 0o600 });
  return { ...f, id, directory, environment, environments, python, manifest };
}

test("inventory reads agree with complete private hashed bytes without changing activation", () => {
  const f = fixture();
  try {
    assert.deepEqual(f.inventory.active(), f.active);
    assert.deepEqual(f.inventory.verify(f.id).manifest, f.manifest);
    assert.deepEqual(f.inventory.active(), f.active);
  } finally {
    f.close();
  }
});

for (const [name, fields] of [
  ["version newline", { core_version: "0.7.12\n" }],
  ["digest newline", { ui_sha256: `${"a".repeat(64)}\n` }],
  ["unknown field", { ignored: true }],
  ["unknown schema", { schema: 2 }],
  ["invalid version", { core_version: "current" }],
  ["relative interpreter", { python: "python" }],
  ["changed interpreter digest", { python_sha256: "b".repeat(64) }],
  ["empty inventory", { files: {} }],
  ["non-record inventory", { files: [] }],
] as const)
  test(`noncanonical ${name} metadata cannot identify a runtime even with a valid manifest hash`, () => {
    const f = fixture(fields);
    try {
      assert.throws(() => f.inventory.verify(f.id), DeploymentError);
    } finally {
      f.close();
    }
  });

for (const change of [
  "worker",
  "missing",
  "extra",
  "link",
  "hardlink",
  "permissions",
  "directory",
  "interpreter-link",
] as const)
  test(`a ${change} change cannot retain verified inventory identity`, () => {
    const f = fixture();
    try {
      const path = join(f.directory, "worker.py");
      if (change === "worker") writeFileSync(path, "changed code");
      else if (change === "missing") rmSync(path);
      else if (change === "extra") writeFileSync(join(f.directory, "extra.py"), "unknown code", { mode: 0o600 });
      else if (change === "link") {
        renameSync(path, join(f.root, "outside.py"));
        symlinkSync(join(f.root, "outside.py"), path);
      } else if (change === "hardlink") linkSync(path, join(f.root, "alias.py"));
      else if (change === "permissions") chmodSync(join(f.directory, "runtime.json"), 0o644);
      else if (change === "directory") chmodSync(f.directory, 0o755);
      else {
        renameSync(f.python, join(f.root, "outside-python"));
        symlinkSync(join(f.root, "outside-python"), f.python);
      }
      assert.throws(() => f.inventory.verify(f.id), DeploymentError);
    } finally {
      f.close();
    }
  });

test("a canonical lifecycle root cannot be replaced by a link after inventory construction", () => {
  const f = fixture();
  try {
    const moved = join(f.root, "moved");
    renameSync(f.inventory.directory, moved);
    symlinkSync(moved, f.inventory.directory);
    assert.throws(() => f.inventory.active(), DeploymentError);
    assert.throws(() => f.inventory.verify(f.id), DeploymentError);
  } finally {
    f.close();
  }
});

test("an owned environment is accepted only with its complete private file inventory", () => {
  const f = environmentFixture();
  try {
    assert.deepEqual(f.inventory.verify(f.id).manifest, f.manifest);
    writeFileSync(join(f.environment, "unlisted.pth"), "unexpected startup code", { mode: 0o600 });
    assert.throws(() => f.inventory.verify(f.id), DeploymentError);
  } finally {
    f.close();
  }
});

for (const [name, fields] of [
  ["unknown schema", { schema: 2 }],
  ["unknown field", { ignored: true }],
  ["outside root", { directory: "/tmp/other-environment" }],
  ["relative directory", { directory: "environments/current" }],
  ["unrecorded files", { files: [] }],
  ["empty files", { files: {} }],
  ["unrecorded requests", { requested: [] }],
  ["unrecorded versions", { versions: [] }],
  ["unrecorded wheels", { wheel_sha256: [] }],
  ["invalid wheel hash", { wheel_sha256: { heinzel: "c".repeat(63) } }],
] as const)
  test(`a hashed environment with ${name} cannot authorize execution`, () => {
    const f = environmentFixture(fields);
    try {
      assert.throws(() => f.inventory.verify(f.id), DeploymentError);
    } finally {
      f.close();
    }
  });

test("replacing the owned environments root with a link invalidates the runtime", () => {
  const f = environmentFixture();
  try {
    const moved = join(f.root, "moved-environments");
    renameSync(f.environments, moved);
    symlinkSync(moved, f.environments);
    assert.throws(() => f.inventory.verify(f.id), DeploymentError);
  } finally {
    f.close();
  }
});

test("activation metadata is absent or exact, never inferred from malformed pointers", () => {
  const f = fixture();
  try {
    const path = join(f.inventory.directory, "active.json");
    rmSync(path);
    assert.equal(f.inventory.active(), null);
    for (const pointer of [
      null,
      [],
      { ...f.active, generation: 0 },
      { ...f.active, previous: "latest" },
      { ...f.active, extra: true },
    ]) {
      writeFileSync(path, `${JSON.stringify(pointer)}\n`, { mode: 0o600 });
      assert.throws(() => f.inventory.active(), DeploymentError);
    }
  } finally {
    f.close();
  }
});
