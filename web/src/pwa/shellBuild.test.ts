import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { afterEach, expect, it } from "vitest";
import { BUILD_PATTERN, buildId, PLACEHOLDER, stampWorker } from "./shellBuild";

const dirs: string[] = [];
afterEach(() => {
  for (const dir of dirs.splice(0)) rmSync(dir, { recursive: true, force: true });
});

function built(page: string): string {
  const dir = mkdtempSync(join(tmpdir(), "hz-shell-build-"));
  dirs.push(dir);
  mkdirSync(join(dir, "assets"));
  writeFileSync(join(dir, "index.html"), page);
  writeFileSync(join(dir, "offline.html"), "<p>offline</p>");
  writeFileSync(join(dir, "assets", "index-abc.js"), "app()");
  writeFileSync(join(dir, "sw.js"), readFileSync(resolve("public/sw.js"), "utf8"));
  return dir;
}

it("the worker in the source carries the placeholder the build stamps, exactly once", () => {
  expect(readFileSync(resolve("public/sw.js"), "utf8").split(PLACEHOLDER)).toHaveLength(2);
});

it("stamps the worker with the build's id, so another build is another worker and another shell cache", () => {
  const first = built('<script src="/assets/index-abc.js"></script>');
  const same = built('<script src="/assets/index-abc.js"></script>');
  const next = built('<script src="/assets/index-def.js"></script>');
  const id = stampWorker(first);
  const stamped = readFileSync(join(first, "sw.js"), "utf8");
  expect(BUILD_PATTERN.exec(stamped)?.[1]).toBe(id);
  expect(stamped).not.toContain(PLACEHOLDER);
  expect(stamped).toMatch(/^const SHELL = `hz-shell-\$\{BUILD\}`;$/m);
  // The worker's own bytes are not part of the id: the stamp does not change what it stamps.
  expect(buildId(first)).toBe(id);
  expect(buildId(same)).toBe(id);
  expect(buildId(next)).not.toBe(id);
  writeFileSync(join(same, "offline.html"), "<p>offline, new words</p>");
  expect(buildId(same)).not.toBe(id); // a changed offline page alone is a new build
});

it("fails the build when the worker has no placeholder to stamp", () => {
  const dir = built("<p></p>");
  writeFileSync(join(dir, "sw.js"), 'const SHELL = "hz-shell-v4";');
  expect(() => stampWorker(dir)).toThrow(/exactly once/);
});
