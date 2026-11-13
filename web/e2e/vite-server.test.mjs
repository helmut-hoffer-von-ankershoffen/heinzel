import assert from "node:assert/strict";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { test } from "vitest";
import { createJourneyServer } from "./vite-server.mjs";

// Vitest runs in web/; a jsdom import.meta.url is not a file URL.
const here = resolve("e2e");

test("parallel journey servers never share a Vite dependency cache, and each cache is removed on close", async () => {
  const configs = [];
  const fake = async (config) => {
    configs.push(config);
    return { close: async () => {} };
  };
  const one = await createJourneyServer("one", { server: { port: 0 } }, fake);
  const two = await createJourneyServer("one", { server: { port: 0 } }, fake);
  assert.notEqual(configs[0].cacheDir, configs[1].cacheDir);
  assert.ok(!configs[0].cacheDir.includes("node_modules"));
  assert.deepEqual(configs[0].server, { port: 0 });
  assert.ok(existsSync(configs[0].cacheDir));
  await one.close();
  await two.close();
  assert.ok(!existsSync(configs[0].cacheDir) && !existsSync(configs[1].cacheDir));
});

test("a server that fails to start leaves no cache behind", async () => {
  let dir;
  const failing = async (config) => {
    dir = config.cacheDir;
    throw new Error("no port");
  };
  await assert.rejects(createJourneyServer("broken", {}, failing), /no port/);
  assert.ok(!existsSync(dir));
});

test("every e2e driver starts Vite through createJourneyServer", () => {
  const direct = readdirSync(here)
    .filter((name) => name.endsWith(".mjs") && name !== "vite-server.mjs" && !name.endsWith(".test.mjs"))
    .filter((name) =>
      /import\s*\{[^}]*\bcreateServer\b[^}]*\}\s*from\s*"vite"/.test(readFileSync(join(here, name), "utf8")),
    );
  assert.deepEqual(direct, []);
});
