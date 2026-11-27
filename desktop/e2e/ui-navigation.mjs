import { preserveCleanupFailure } from "./controller-gate.mjs";
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
let initialRedirectLifecycleHits = 0;
let subsequentRedirectHits = 0;
let lifecycleHits = 0;
// Per launch: lifecycle hits at its start, and the hits it made before its first page request. The attached service's
// periodic lifecycle probe (every 5 s) adds hits to a long launch, so only the per-launch order is exact.
let launchLifecycleStart = 0;
let firstPageLifecycleHits = null;
const launches = [];
const target = createServer((_request, response) => { targetRequests++; response.writeHead(200); response.end("unexpected"); });
const source = createServer((request, response) => {
  if (request.url === "/" && firstPageLifecycleHits === null) firstPageLifecycleHits = lifecycleHits - launchLifecycleStart;
  if (request.url === "/api/lifecycle") {
    lifecycleHits++;
    response.writeHead(request.headers.authorization === `Bearer ${token}` ? 200 : 403, { "content-type": "application/json" });
    response.end(JSON.stringify({ instance: "isolated", version: "0.0.0" }));
  } else if ((request.url === "/" && initialRedirect) || request.url === "/redirect") {
    if (initialRedirect && request.url === "/") { initialRedirectHits++; initialRedirectLifecycleHits = lifecycleHits - launchLifecycleStart; }
    if (request.url === "/redirect") subsequentRedirectHits++;
    response.writeHead(302, { location: `http://127.0.0.1:${target.address().port}/target` });
    response.end();
  } else {
    response.writeHead(200, { "content-type": "text/html" });
    response.end(`<!doctype html><title>Isolated fixture</title><p>ready</p>
      <a id="capture" data-hz-public-media target="_blank" rel="noreferrer noopener" href="https://raw.githubusercontent.com/fictional-lab/harbor/${"a".repeat(40)}/docs/ui.png">Full-size capture</a>
      <a id="sound" data-hz-public-media target="_blank" rel="noreferrer noopener" href="https://www.youtube.com/watch?v=abcdefghijk">Soundscape</a>
      <a id="failed" data-hz-public-media target="_blank" rel="noreferrer noopener" href="https://www.youtube.com/watch?v=abcdefghijl">Unavailable opener</a>
      <a id="unsafe" data-hz-public-media target="_blank" href="http://127.0.0.1:${target.address().port}/target">Refused target</a>
      <a id="unmarked" target="_blank" href="http://127.0.0.1:${target.address().port}/target">Unmarked popup</a>
      <output id="result"></output><script>
        for (const link of document.querySelectorAll('a')) link.addEventListener('hz-public-media-result', (event) => {
          if (event.detail === false) document.querySelector('#result').textContent = 'refused';
        });
      </script>`);
  }
});
await Promise.all([listen(target), listen(source)]);
try {
  await run(false);
  await run(true);
  const [navigation, redirect] = launches;
  assert.equal(targetRequests, 0, "Cross-origin target received a request.");
  assert.equal(initialRedirectHits, 1, "The initial redirect was not exercised.");
  assert.equal(navigation.beforePage, 1, "The first page must follow exactly one authenticated lifecycle verification.");
  assert.ok(navigation.total >= 2, "The attached service's periodic lifecycle probe did not run during the long launch.");
  assert.equal(redirect.beforePage, 1, "The initial redirect must follow authenticated lifecycle verification.");
  assert.equal(initialRedirectLifecycleHits, 1, "The initial redirect must follow authenticated lifecycle verification.");
  assert.equal(subsequentRedirectHits, 1, "The page redirect was not exercised.");
  assert.ok(redirect.total >= 2, "The recovery picker must verify lifecycle after the rejected redirect.");
  console.log("Electron handed off trusted public media, refused scripted/unsafe opening, and blocked page navigation, redirects and popups before the target request.");
} finally {
  await Promise.all([close(source), close(target)]);
}

async function run(redirectOnStart) {
  initialRedirect = redirectOnStart;
  launchLifecycleStart = lifecycleHits;
  firstPageLifecycleHits = null;
  try {
    await launch(redirectOnStart);
  } finally {
    launches.push({ beforePage: firstPageLifecycleHits, total: lifecycleHits - launchLifecycleStart });
  }
}

async function launch(redirectOnStart) {
  const root = await mkdtemp(join(tmpdir(), "hz-navigation-isolated-"));
  let app, processHandle, stopped = false, launchAttempted = false, primary;
  try {
    const dirs = ["bin", "home", "config", "data", "state", "cache", "logs", "tmp", "electron-profile", "electron-session", "electron-logs"];
    await Promise.all(dirs.map((name) => mkdir(join(root, name), { mode: 0o700 })));
    const cli = join(root, "bin", "hz");
    await writeFile(cli, `#!${process.execPath}\nif (JSON.stringify(process.argv.slice(2)) !== JSON.stringify(["ui","attach","--bridge-token-stdin"])) process.exit(64);\nprocess.stdout.write(process.env.HZ_TEST_URL + "\\n", () => process.exit(0));\n`, { mode: 0o700 });
    const env = {
      PATH: `${join(root, "bin")}${delimiter}/usr/bin:/bin:/usr/sbin:/sbin`, HOME: join(root, "home"), TMPDIR: join(root, "tmp"),
      USER: process.env.USER || "", LOGNAME: process.env.LOGNAME || "", LANG: "en_US.UTF-8",
      XDG_CONFIG_HOME: join(root, "config"), XDG_DATA_HOME: join(root, "data"), XDG_STATE_HOME: join(root, "state"), XDG_CACHE_HOME: join(root, "cache"),
      HZ_CONFIG_HOME: join(root, "config"), HZ_DATA_DIR: join(root, "data"), HZ_STATE_DIR: join(root, "state"), HZ_CACHE_DIR: join(root, "cache"), HZ_LOG_DIR: join(root, "logs"),
      HZ_NATIVE_TEST_ROOT: root, HZ_NATIVE_TEST_CONTROLLER_GATE: "1", HZ_TEST_URL: `http://127.0.0.1:${source.address().port}/#token=${token}`,
      HZ_NATIVE_TEST_PUBLIC_MEDIA: "1",
      ...(process.env.DISPLAY ? { DISPLAY: process.env.DISPLAY } : {}), ...(process.env.XAUTHORITY ? { XAUTHORITY: process.env.XAUTHORITY } : {}),
    };
    launchAttempted = true;
    const attachStarted = performance.now();
    console.log(`Electron navigation phase: ${redirectOnStart ? "initial-redirect" : "page-navigation"} attach`);
    app = await electron.launch({ executablePath: executable, args: [fileURLToPath(new URL("./real-backend-bootstrap.mjs", import.meta.url))], cwd: root, env, timeout: 30_000 });
    processHandle = app.process();
    // Playwright's node-context listener must settle before a deliberate early app exit.
    await withTimeout(app.evaluate(() => true), Math.max(1, 30_000 - (performance.now() - attachStarted)),
      "Electron test controller did not attach within the original launch budget.");
    await writeFile(join(root, "controller-ready"), "ready", { flag: "wx", mode: 0o600 });
    console.log(`Electron navigation phase: ${redirectOnStart ? "initial-redirect" : "page-navigation"} attached`);
    if (redirectOnStart) {
      const pickerUrl = new URL("../src/workspace-picker.html", import.meta.url).href;
      let picker;
      const deadline = performance.now() + 15_000;
      while (!picker && performance.now() < deadline) {
        picker = app.windows().find((candidate) => candidate.url() === pickerUrl);
        if (!picker) await new Promise((done) => setTimeout(done, 50));
      }
      assert.ok(picker, "Electron did not open the recovery picker after the rejected initial redirect.");
      await picker.locator("#retry").waitFor({ state: "visible", timeout: 15_000 });
      await picker.locator("#status").filter({ hasText: "Connection restored. Try again to reopen the workspace." })
        .waitFor({ state: "visible", timeout: 15_000 });
      assert.equal(targetRequests, 0, "The rejected redirect reached its target.");
      assert.deepEqual(app.windows().map((page) => page.url()), [pickerUrl], "The failed product window remained open.");
      assert.equal(await picker.evaluate(() => typeof window.heinzelDesktop), "undefined");
      const exited = new Promise((done) => processHandle.once("exit", (code, signal) => done({ code, signal })));
      await picker.close();
      const result = await withTimeout(exited, 15_000, "Electron did not exit after closing the recovery picker.");
      stopped = true;
      assert.equal(result.code, 0);
      assert.equal(result.signal, null);
    } else {
      const page = await app.firstWindow({ timeout: 30_000 });
      await page.locator("p").waitFor();
      // Hold this launch past one probe interval on every runner, so the periodic lifecycle probe is always part of
      // the proof and never only on a slow runner (a fast runner hid that it adds to the lifecycle count).
      let probeDeadline = false;
      await withTimeout((async () => {
        while (!probeDeadline && lifecycleHits - launchLifecycleStart < 2) await new Promise((done) => setTimeout(done, 50));
      })(), 15_000, "The attached service's periodic lifecycle probe did not reach the isolated service.")
        .finally(() => { probeDeadline = true; });
      assert.deepEqual(await page.evaluate(() => Object.keys(window.heinzelDesktop)), [
        "clientFlavor",
        "workspaceAudio", "titlebar", "browser", "player", "linkMode", "lifecycle", "about", "systemAudio", "audioSource",
        "onFullscreen",
      ]);
      assert.equal(await page.evaluate(() => window.heinzelDesktop.clientFlavor), "electron");
      await page.evaluate(() => document.querySelector("#sound").click());
      assert.equal(await readFile(join(root, "public-media"), "utf8").catch(() => ""), "", "A scripted click reached the OS opener.");
      await page.locator("#capture").click();
      await page.locator("#sound").focus();
      await page.keyboard.press("Enter");
      await page.locator("#failed").click();
      await page.locator("#result").filter({ hasText: "refused" }).waitFor();
      const handedOff = (await readFile(join(root, "public-media"), "utf8")).trim().split("\n").map((line) => JSON.parse(line));
      assert.deepEqual(handedOff, [
        `https://raw.githubusercontent.com/fictional-lab/harbor/${"a".repeat(40)}/docs/ui.png`,
        "https://www.youtube.com/watch?v=abcdefghijk", "https://www.youtube.com/watch?v=abcdefghijl",
      ]);
      await page.locator("#result").evaluate((element) => { element.textContent = ""; });
      await page.locator("#unsafe").click();
      await page.locator("#result").filter({ hasText: "refused" }).waitFor();
      assert.equal((await readFile(join(root, "public-media"), "utf8")).trim().split("\n").length, 3);
      await page.locator("#unmarked").click();
      await page.evaluate((url) => { window.open(url, "_blank"); }, `http://127.0.0.1:${target.address().port}/target`);
      assert.equal(app.windows().length, 1, "A denied popup created another native window.");
      assert.equal((await readFile(join(root, "public-media"), "utf8")).trim().split("\n").length, 3);
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
    const ownedPid = Number(await readFile(join(root, "owned-pid"), "utf8"));
    assert.equal(processExists(ownedPid), false, "Electron left its attach command running.");
    assert.deepEqual(JSON.parse(await readFile(join(root, "owned-exit"), "utf8")), { code: 0, signal: null });
    const paths = JSON.parse(await readFile(join(root, "electron-paths"), "utf8"));
    assert.deepEqual(paths, {
      userData: join(root, "electron-profile"),
      sessionData: join(root, "electron-session"),
    });
  } catch (error) {
    primary = error;
    console.error(`Electron navigation requests: ${JSON.stringify({ initialRedirectHits, subsequentRedirectHits, lifecycleHits, firstPageLifecycleHits, targetRequests })}`);
    console.error(`Electron phases:\n${await readFile(join(root, "phases"), "utf8").catch(() => "none")}`);
    throw error;
  } finally {
    await preserveCleanupFailure(primary, () => cleanupHarness({ app, electronProcess: processHandle, stopped, root, launchAttempted,
      loadPid: async () => Number(await readFile(join(root, "owned-pid"), "utf8")), processExists,
      remove: (directory) => rm(directory, { recursive: true, force: true }) }));
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
