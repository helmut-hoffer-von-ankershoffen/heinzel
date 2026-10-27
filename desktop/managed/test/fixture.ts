import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import {
  chmodSync,
  copyFileSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  realpathSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { RuntimeInventory, type RuntimeManifest } from "../src/inventory.js";
import { openProfile } from "../src/profile.js";

export const digest = (value: string | Buffer) => createHash("sha256").update(value).digest("hex");

export function record(value: unknown): Record<string, unknown> {
  assert.ok(value && typeof value === "object" && !Array.isArray(value));
  return value as Record<string, unknown>;
}

/** Only tests call the existing transition authority; the managed library has no transition-writing interface. */
export function fixture(workerOverride?: string, copiedInterpreter = false) {
  const root = realpathSync(mkdtempSync(join(tmpdir(), "hz-managed-backend-")));
  chmodSync(root, 0o700);
  const profile = openProfile(join(root, "profiles"), { name: "test", channel: "development" });
  const inventory = new RuntimeInventory(join(profile.directory, "lifecycle"));
  const basePython = realpathSync(resolve("../../.venv/bin/python"));
  const python = copiedInterpreter ? join(profile.directory, "python") : basePython;
  if (copiedInterpreter) {
    copyFileSync(basePython, python);
    chmodSync(python, 0o700);
  }
  const worker = workerOverride ?? readFileSync("../../tests/fixtures/managed_backend_http.py");
  const manifest: RuntimeManifest = {
    schema: 1,
    core_version: "0.7.12",
    python,
    python_sha256: digest(readFileSync(python)),
    ui_sha256: digest(
      '<!doctype html><html><head><title>Fixture</title></head><body>verified fixture app<pre id="state">Loading</pre><script>window.hzDesktop.snapshot().then(value => document.querySelector("#state").textContent = JSON.stringify(value));</script></body></html>',
    ),
    files: { "worker.py": digest(worker) },
  };
  const encoded = `${JSON.stringify(manifest)}\n`,
    id = digest(encoded);
  const directory = join(inventory.directory, "deployments", id);
  mkdirSync(directory, { mode: 0o700 });
  writeFileSync(join(directory, "worker.py"), worker, { mode: 0o600 });
  writeFileSync(join(directory, "runtime.json"), encoded, { mode: 0o600 });
  const control = (action: string, fields: Record<string, unknown> = {}): unknown => {
    const result: unknown = JSON.parse(
      execFileSync(resolve("../../.venv/bin/python"), ["-I", "-B", "-m", "heinzel._lifecycle_control"], {
        input: JSON.stringify({ directory: inventory.directory, action, ...fields }),
        encoding: "utf8",
        timeout: 15_000,
        env: { HOME: root, PATH: "/usr/bin:/bin", LANG: "C.UTF-8" },
        stdio: ["pipe", "pipe", "pipe"],
      }),
    );
    const answer = record(result);
    if (answer.ok !== true) throw new Error("The shared lifecycle authority refused the test transition.");
    return answer.value;
  };
  const plan = record(control("prepare", { candidate: id, expected: null }));
  control("switch", { id: plan.id });
  const active = inventory.active();
  assert.ok(active);
  const complete = (instance = "a".repeat(32)) =>
    control("complete", {
      id: plan.id,
      proof: {
        deployment: id,
        generation: active.generation,
        instance,
        version: manifest.core_version,
        ui_sha256: manifest.ui_sha256,
      },
    });
  const work = join(profile.directory, "work.txt");
  writeFileSync(work, "retained user work", { mode: 0o600 });
  return {
    root,
    profile,
    inventory,
    python,
    manifest,
    id,
    directory,
    active,
    control,
    complete,
    work,
    close: () => rmSync(root, { recursive: true, force: true }),
  };
}
