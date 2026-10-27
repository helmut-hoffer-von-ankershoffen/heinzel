import assert from "node:assert/strict";
import { existsSync, type FSWatcher, readFileSync, unlinkSync, watch, writeFileSync } from "node:fs";
import { createConnection } from "node:net";
import { join } from "node:path";
import test from "node:test";
import { BackendError, OwnedBackend } from "../src/backend.js";
import { BROKER_CODE, WORKER_CODE } from "../src/runtime-code.js";
import { fixture, record } from "./fixture.js";

test("sealed broker and worker strings match the reviewed standalone source", () => {
  assert.equal(BROKER_CODE, readFileSync("runtime/broker.py", "utf8"));
  assert.equal(WORKER_CODE, readFileSync("runtime/worker.py", "utf8"));
});

test("the parent reports authenticated readiness only for its inventoried owned worker", {
  timeout: 15_000,
}, async () => {
  const f = fixture();
  const backend = new OwnedBackend(f.profile, f.inventory, 0);
  try {
    const endpoint = await backend.start(f.active);
    assert.match(endpoint.origin, /^http:\/\/127\.0\.0\.1:\d+$/);
    assert.equal(endpoint.version, f.manifest.core_version);
    assert.equal(endpoint.ui_sha256, f.manifest.ui_sha256);
    assert.equal(endpoint.deployment, f.id);
    assert.equal(endpoint.generation, f.active.generation);
    assert.equal((await backend.start()).instance, endpoint.instance);
    const token = new URLSearchParams(new URL(endpoint.url).hash.slice(1)).get("token");
    const response = await fetch(`${endpoint.origin}/api/lifecycle`, { headers: { Authorization: `Bearer ${token}` } });
    assert.equal(response.status, 200);
    assert.equal(record(await response.json()).instance, endpoint.instance);
    assert.equal((await fetch(`${endpoint.origin}/api/lifecycle`)).status, 401);
    f.complete(endpoint.instance);
    const prepared = record(f.control("prepare", { candidate: f.id, expected: f.inventory.active() }));
    assert.throws(() => f.control("switch", { id: prepared.id }), /refused/);
    await backend.stop({ requireGraceful: true });
    assert.equal(backend.endpoint, null);
    assert.equal(backend.running, false);
    await assert.rejects(fetch(`${endpoint.origin}/api/lifecycle`, { signal: AbortSignal.timeout(1000) }));
    assert.equal(readFileSync(f.work, "utf8"), "retained user work");
    assert.equal(f.inventory.active()?.generation, f.active.generation);
  } finally {
    await backend.stop();
    f.close();
  }
});

test("a later controller reuses the explicitly selected origin after owned stop", { timeout: 15_000 }, async () => {
  const f = fixture();
  const first = new OwnedBackend(f.profile, f.inventory, 0);
  let second: OwnedBackend | undefined;
  try {
    const before = await first.start();
    f.complete(before.instance);
    await first.stop({ requireGraceful: true });
    second = new OwnedBackend(f.profile, f.inventory, Number(new URL(before.origin).port));
    const after = await second.start(f.active);
    assert.equal(after.origin, before.origin);
    assert.notEqual(after.instance, before.instance);
    assert.deepEqual(f.inventory.active(), f.active);
    assert.equal(readFileSync(f.work, "utf8"), "retained user work");
  } finally {
    await first.stop();
    await second?.stop();
    f.close();
  }
});

test("a stale activation cannot create a helper or spawn a replacement worker", async () => {
  const f = fixture();
  try {
    f.complete();
    const next = record(f.control("prepare", { candidate: f.id, expected: f.active }));
    f.control("switch", { id: next.id });
    const backend = new OwnedBackend(f.profile, f.inventory, 0);
    await assert.rejects(backend.start(f.active), BackendError);
    assert.equal(backend.running, false);
    assert.equal(existsSync(join(f.profile.directory, "helpers")), false);
    assert.equal(f.inventory.active()?.generation, f.active.generation + 1);
  } finally {
    f.close();
  }
});

test("changed runtime bytes cannot become a ready endpoint", async () => {
  const f = fixture();
  try {
    writeFileSync(join(f.directory, "worker.py"), "changed owned worker");
    const backend = new OwnedBackend(f.profile, f.inventory, 0);
    await assert.rejects(backend.start());
    assert.equal(backend.endpoint, null);
    assert.equal(backend.running, false);
  } finally {
    f.close();
  }
});

test("invalid port selection fails before any process ownership", () => {
  const f = fixture();
  try {
    for (const port of [-1, 65536, 1.5, Number.NaN])
      assert.throws(() => new OwnedBackend(f.profile, f.inventory, port), BackendError);
  } finally {
    f.close();
  }
});

test("stop during a held readiness probe cannot return a retired endpoint", { timeout: 15_000 }, async () => {
  const worker = readFileSync("../../tests/fixtures/managed_backend_http.py", "utf8")
    .replace("import json", "import json\nfrom pathlib import Path")
    .replace(
      "            body = json.dumps",
      "            if not Path('probe-entered').exists():\n                with socket.socket() as gate:\n                    gate.bind(('127.0.0.1', 0))\n                    gate.listen(1)\n                    Path('probe-ready.tmp').write_text(str(gate.getsockname()[1]))\n                    os.replace('probe-ready.tmp', 'probe-entered')\n                    release, _ = gate.accept()\n                    with release:\n                        release.recv(1)\n            body = json.dumps",
    );
  const f = fixture(worker);
  const backend = new OwnedBackend(f.profile, f.inventory, 0);
  let watcher: FSWatcher | undefined;
  let deadline: NodeJS.Timeout | undefined;
  try {
    const entered = new Promise<void>((resolve, reject) => {
      watcher = watch(f.profile.directory, (_event, name) => {
        if (name === "probe-entered") {
          watcher?.close();
          clearTimeout(deadline);
          resolve();
        }
      });
      deadline = setTimeout(() => reject(new Error("The readiness probe did not enter its explicit gate")), 5000);
    });
    const becameReady = backend.start().then(
      () => true,
      () => false,
    );
    await entered;
    const stopping = backend.stop();
    const port = Number(readFileSync(join(f.profile.directory, "probe-entered"), "utf8"));
    await new Promise<void>((resolve, reject) => {
      const gate = createConnection({ host: "127.0.0.1", port });
      gate.once("connect", () => gate.end("x"));
      gate.once("close", () => resolve());
      gate.once("error", reject);
    });
    const ready = await becameReady;
    await stopping;
    assert.equal(ready, false, "a stopped controller cannot publish readiness observed during its drain");
    assert.equal(backend.endpoint, null);
    assert.equal(backend.running, false);
  } finally {
    watcher?.close();
    clearTimeout(deadline);
    await backend.stop();
    f.close();
  }
});

test("an interpreter lost after verification releases failed-spawn ownership instead of waiting for an exit event", {
  timeout: 5000,
}, async () => {
  const f = fixture(undefined, true);
  const backend = new OwnedBackend(
    f.profile,
    {
      active: () => f.inventory.active(),
      verify: (id) => {
        const checked = f.inventory.verify(id);
        unlinkSync(f.python);
        return checked;
      },
    },
    0,
  );
  try {
    await assert.rejects(backend.start(), BackendError);
    assert.equal(backend.running, false);
    assert.equal(backend.endpoint, null);
    await assert.rejects(backend.stop({ requireGraceful: true }), BackendError);
    assert.equal(readFileSync(f.work, "utf8"), "retained user work");
  } finally {
    await backend.stop();
    f.close();
  }
});
