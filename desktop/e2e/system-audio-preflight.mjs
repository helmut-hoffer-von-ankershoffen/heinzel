// Capture-free preflight for the native system-audio study. No media request is made here.
import assert from "node:assert/strict";
import { constants } from "node:fs";
import { access, mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { delimiter, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { cleanupHarness, withTimeout } from "./harness-cleanup.mjs";

if (process.env.CI !== "true" || process.env.HZ_DISPOSABLE_NATIVE_CI !== "1" ||
    process.platform !== "darwin" || process.arch !== "arm64") {
  throw new Error("This fixture only runs on an explicitly disposable macOS ARM CI runner.");
}
const { _electron: electron } = await import("../../web/node_modules/playwright-core/index.mjs");
const desktop = fileURLToPath(new URL("../", import.meta.url));
const electronDir = join(desktop, "node_modules/electron");
assert.equal(JSON.parse(await readFile(join(electronDir, "package.json"), "utf8")).version, "45.0.0");
const dist = join(electronDir, "dist");
const executable = resolve(dist, (await readFile(join(electronDir, "path.txt"), "utf8")).trim());
assert.ok(!relative(dist, executable).startsWith(".."), "Electron executable must belong to its locked package");
await access(executable, constants.X_OK);
const root = await mkdtemp(join(tmpdir(), "hz-native-audio-preflight-"));
const token = "fictional_audio_preflight_token_1234567890";
let app, child, launchAttempted = false;
const server = createServer((request, response) => {
  if (request.url === "/") {
    response.writeHead(200, { "content-type": "text/html", "content-security-policy": "default-src 'none'" });
    response.end("<!doctype html><title>Fictional audio preflight</title><main>Isolated fictional surface</main>");
  } else if (request.url === "/api/lifecycle" && request.headers.authorization === `Bearer ${token}`) {
    response.writeHead(200, { "content-type": "application/json" });
    response.end(JSON.stringify({ instance: "fictional", version: "0.0.0" }));
  } else {
    response.writeHead(404); response.end();
  }
});
try {
  for (const name of ["electron-profile", "electron-session", "electron-logs", "bin", "home", "tmp", "config", "state", "data", "cache"]) await mkdir(join(root, name));
  await new Promise((resolveListen, reject) => { server.once("error", reject); server.listen(0, "127.0.0.1", resolveListen); });
  const origin = `http://127.0.0.1:${server.address().port}`;
  const cli = join(root, "bin", "hz");
  await writeFile(cli, `#!${process.execPath}\nprocess.stdout.write(process.env.HZ_TEST_URL+'\\n');\nprocess.on('SIGINT',()=>process.exit(0));\nsetInterval(()=>{},1000);\n`, { mode: 0o700 });
  launchAttempted = true;
  app = await electron.launch({ executablePath: executable, args: [join(desktop, "e2e/real-backend-bootstrap.mjs")], cwd: root, timeout: 30000,
    env: { PATH: `${join(root, "bin")}${delimiter}/usr/bin:/bin`, HOME: join(root, "home"), TMPDIR: join(root, "tmp"), USER: process.env.USER || "", LOGNAME: process.env.LOGNAME || "", LANG: "en_US.UTF-8", XDG_CONFIG_HOME: join(root, "config"), XDG_STATE_HOME: join(root, "state"), XDG_DATA_HOME: join(root, "data"), XDG_CACHE_HOME: join(root, "cache"), HZ_NATIVE_TEST_ROOT: root, HZ_TEST_URL: `${origin}/#token=${token}` } });
  child = app.process();
  const page = await app.firstWindow({ timeout: 30000 });
  await withTimeout(page.waitForFunction(() => document.title === "Fictional audio preflight"), 10000, "Fictional surface did not load");
  assert.equal(new URL(page.url()).origin, origin);
  assert.equal(await page.locator("main").textContent(), "Isolated fictional surface");
  assert.equal(await page.evaluate(() => window.heinzelDesktop.workspaceAudio), true);
  assert.equal(typeof await page.evaluate(() => window.heinzelDesktop.systemAudio()), "boolean");
  console.log("Fictional owned surface loaded; no media capture requested.");
} finally {
  try {
    await cleanupHarness({ app, electronProcess: child, launchAttempted, root,
      loadPid: async () => Number(await readFile(join(root, "owned-pid"), "utf8")),
      processExists: (pid) => { try { process.kill(pid, 0); return true; } catch (error) { if (error.code === "ESRCH") return false; throw error; } },
      remove: (path) => rm(path, { recursive: true, force: true }), timeoutMs: 10000 });
  } finally {
    if (server.listening) { server.closeAllConnections(); await new Promise((resolveClose) => server.close(resolveClose)); }
  }
}
console.log("Owned Electron and service exited; isolated profile removed.");
