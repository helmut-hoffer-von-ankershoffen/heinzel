// Isolated real-Electron navigation proof: no installed service or production profile is touched.
import assert from "node:assert/strict";
import { randomBytes } from "node:crypto";
import { constants } from "node:fs";
import { access, mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { delimiter, isAbsolute, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { _electron as electron } from "../../web/node_modules/playwright-core/index.mjs";
import { cleanupHarness, withTimeout } from "./harness-cleanup.mjs";

const electronDirectory = process.env.HZ_TEST_ELECTRON_DIR ?? fileURLToPath(new URL("../node_modules/electron/", import.meta.url));
if (!isAbsolute(electronDirectory)) throw new Error("Electron test directory must be absolute.");
const manifest = JSON.parse(await readFile(join(electronDirectory, "package.json"), "utf8"));
if (manifest.version !== "45.0.0") throw new Error("Electron 45.0.0 is required.");
const dist = join(electronDirectory, "dist");
const path = (await readFile(join(electronDirectory, "path.txt"), "utf8")).trim();
const executable = resolve(dist, path);
if (!path || isAbsolute(path) || relative(dist, executable).startsWith("..")) throw new Error("Invalid Electron path.");
await access(executable, constants.X_OK);
const token = randomBytes(24).toString("base64url");
let targetRequests = 0;
let initialRedirect = false;
let initialRedirectHits = 0;
let subsequentRedirectHits = 0;
let lifecycleHits = 0;
const target = createServer((_request, response) => { targetRequests++; response.writeHead(200); response.end("unexpected"); });
const source = createServer((request, response) => {
  if (request.url === "/api/lifecycle") {
    lifecycleHits++;
    response.writeHead(request.headers.authorization === `Bearer ${token}` ? 200 : 403, { "content-type": "application/json" });
    response.end(JSON.stringify({ instance: "isolated", version: "0.0.0" }));
  } else if ((request.url === "/" && initialRedirect) || request.url === "/redirect") {
    if (initialRedirect && request.url === "/") initialRedirectHits++;
    if (request.url === "/redirect") subsequentRedirectHits++;
    response.writeHead(302, { location: `http://127.0.0.1:${target.address().port}/target` });
    response.end();
  } else {
    response.writeHead(200, { "content-type": "text/html" });
    response.end("<!doctype html><title>Isolated fixture</title><p>ready</p>");
  }
});
await Promise.all([listen(target), listen(source)]);
try {
  await run(false);
  await run(true);
  assert.equal(targetRequests, 0, "Cross-origin target received a request.");
  assert.equal(initialRedirectHits, 1, "The initial redirect was not exercised.");
  assert.equal(subsequentRedirectHits, 1, "The page redirect was not exercised.");
  assert.equal(lifecycleHits, 2, "Both launches must verify lifecycle first.");
  console.log("Electron blocked page navigation, same-origin redirect, and initial authenticated redirect before the target request.");
} finally {
  await Promise.all([close(source), close(target)]);
}

async function run(redirectOnStart) {
  initialRedirect = redirectOnStart;
  const root = await mkdtemp(join(tmpdir(), "hz-navigation-isolated-"));
  let app, processHandle, stopped = false, launchAttempted = false;
  try {
    const dirs = ["bin", "home", "config", "data", "state", "cache", "logs", "tmp", "electron-profile", "electron-session", "electron-logs"];
    await Promise.all(dirs.map((name) => mkdir(join(root, name), { mode: 0o700 })));
    const cli = join(root, "bin", "hz");
    await writeFile(cli, `#!${process.execPath}\nif (JSON.stringify(process.argv.slice(2)) !== JSON.stringify(["ui","--port","0","--no-browser"])) process.exit(64);\nprocess.stdout.write(process.env.HZ_TEST_URL + "\\n");\nprocess.on("SIGINT", () => process.exit(0));\nsetInterval(() => {}, 1000);\n`, { mode: 0o700 });
    const env = {
      PATH: `${join(root, "bin")}${delimiter}/usr/bin:/bin:/usr/sbin:/sbin`, HOME: join(root, "home"), TMPDIR: join(root, "tmp"),
      USER: process.env.USER || "", LOGNAME: process.env.LOGNAME || "", LANG: "en_US.UTF-8",
      XDG_CONFIG_HOME: join(root, "config"), XDG_DATA_HOME: join(root, "data"), XDG_STATE_HOME: join(root, "state"), XDG_CACHE_HOME: join(root, "cache"),
      HZ_CONFIG_HOME: join(root, "config"), HZ_DATA_DIR: join(root, "data"), HZ_STATE_DIR: join(root, "state"), HZ_CACHE_DIR: join(root, "cache"), HZ_LOG_DIR: join(root, "logs"),
      HZ_NATIVE_TEST_ROOT: root, HZ_TEST_URL: `http://127.0.0.1:${source.address().port}/#token=${token}`,
      ...(process.env.DISPLAY ? { DISPLAY: process.env.DISPLAY } : {}), ...(process.env.XAUTHORITY ? { XAUTHORITY: process.env.XAUTHORITY } : {}),
    };
    launchAttempted = true;
    app = await electron.launch({ executablePath: executable, args: [fileURLToPath(new URL("./real-backend-bootstrap.mjs", import.meta.url))], cwd: root, env, timeout: 30_000 });
    processHandle = app.process();
    assert.equal(await app.evaluate(({ app: current }) => current.getPath("userData")), join(root, "electron-profile"));
    if (redirectOnStart) {
      const exited = new Promise((done) => processHandle.once("exit", done));
      await withTimeout(exited, 15_000, "Electron did not stop after the rejected initial redirect.");
      stopped = true;
    } else {
      const page = await app.firstWindow({ timeout: 30_000 });
      await page.locator("p").waitFor();
      await app.evaluate(({ BrowserWindow }) => {
        const contents = BrowserWindow.getAllWindows()[0].webContents;
        globalThis.navigationEvents = [];
        for (const name of ["will-navigate", "will-redirect"]) contents.on(name, (event) => {
          globalThis.navigationEvents.push({ name, url: event.url, isMainFrame: event.isMainFrame });
        });
      });
      const targetUrl = `http://127.0.0.1:${target.address().port}/target`;
      await page.evaluate((url) => { location.href = url; }, targetUrl);
      await waitEvent(app, "will-navigate", targetUrl);
      assert.equal(targetRequests, 0);
      await page.evaluate(() => { location.href = "/redirect"; });
      await waitEvent(app, "will-redirect", targetUrl);
      assert.equal(targetRequests, 0);
      assert.equal(new URL(page.url()).origin, `http://127.0.0.1:${source.address().port}`);
      const exited = new Promise((done) => processHandle.once("exit", done));
      await page.close();
      await withTimeout(exited, 15_000, "Electron did not exit after closing its window.");
      stopped = true;
    }
  } finally {
    await cleanupHarness({ app, electronProcess: processHandle, stopped, root, launchAttempted,
      loadPid: async () => Number(await readFile(join(root, "owned-pid"), "utf8")), processExists,
      remove: (directory) => rm(directory, { recursive: true, force: true }) });
  }
}
async function waitEvent(app, name, url) {
  await withTimeout((async () => {
    while (!(await app.evaluate((_, expected) => globalThis.navigationEvents.some((event) => event.name === expected.name && event.url === expected.url && event.isMainFrame === true), { name, url })))
      await new Promise((done) => setTimeout(done, 50));
  })(), 5000, `Electron did not emit the expected ${name} details.`);
}
function processExists(pid) {
  try { process.kill(pid, 0); return true; } catch (error) { if (error.code === "ESRCH") return false; throw error; }
}
function listen(server) { return new Promise((done, fail) => { server.once("error", fail); server.listen(0, "127.0.0.1", done); }); }
function close(server) { return new Promise((done) => server.close(done)); }
