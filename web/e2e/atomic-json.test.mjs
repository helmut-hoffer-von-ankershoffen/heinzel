import assert from "node:assert/strict";
import * as fs from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { publishJson } from "./atomic-json.mjs";

test("the daemon start is invisible until complete JSON is ready", () => {
  const root = fs.mkdtempSync(join(tmpdir(), "hz-atomic-json-"));
  const target = join(root, "daemon-start.json");
  try {
    let checked = false;
    const io = {
      ...fs,
      renameSync(staged, destination) {
        assert.equal(destination, target);
        assert.equal(fs.existsSync(target), false);
        assert.deepEqual(JSON.parse(fs.readFileSync(staged, "utf8")), { session: "fixture" });
        checked = true;
        fs.renameSync(staged, destination);
      },
    };
    publishJson(target, { session: "fixture" }, io);
    assert.equal(checked, true);
    assert.deepEqual(JSON.parse(fs.readFileSync(target, "utf8")), { session: "fixture" });
    assert.deepEqual(fs.readdirSync(root), ["daemon-start.json"]);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
