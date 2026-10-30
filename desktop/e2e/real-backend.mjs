// Explicit opt-in real local CLI smoke: no production profile, configured service, token, or listener is used.
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { access, mkdir, mkdtemp, readFile, rm, symlink, writeFile } from "node:fs/promises";
import { constants } from "node:fs";
import { connect } from "node:net";
import { tmpdir } from "node:os";
import { isAbsolute, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { _electron as electron } from "../../web/node_modules/playwright-core/index.mjs";

const electronDirectory = fileURLToPath(new URL("../node_modules/electron/", import.meta.url));
const electronDist = join(electronDirectory, "dist");
const electronPath = (await readFile(join(electronDirectory, "path.txt"), "utf8")).trim();
const electronExecutable = resolve(electronDist, electronPath);
if (!electronPath || isAbsolute(electronPath) || relative(electronDist, electronExecutable).startsWith(".."))
  throw new Error("The locked Electron executable path is invalid.");
await access(electronExecutable, constants.X_OK);
const cli = process.env.HZ_REAL_CLI;
if (!cli || !isAbsolute(cli)) throw new Error("Set HZ_REAL_CLI to the absolute installed hz executable.");
await access(cli, constants.X_OK);
const root = await mkdtemp(join(tmpdir(), "hz-electron-isolated-"));
const dirs = ["bin", "home", "config", "data", "state", "cache", "logs", "electron-profile", "electron-session", "electron-logs", "tmp"];
let app;
let ownedPid;
let stopped = false;
try {
  await Promise.all(dirs.map((name) => mkdir(join(root, name), { mode: 0o700 })));
  await symlink(cli, join(root, "bin", "hz"));
  await writeFile(join(root, "config", "config.toml"), '[hz]\ndisabled_modules = ["tailscale", "clock"]\nupdate_check = false\n', { mode: 0o600 });
  const env = {
    HOME: join(root, "home"), TMPDIR: join(root, "tmp"), PATH: `${join(root, "bin")}:/usr/bin:/bin:/usr/sbin:/sbin`,
    USER: process.env.USER || "", LOGNAME: process.env.LOGNAME || "", LANG: "en_US.UTF-8",
    XDG_CONFIG_HOME: join(root, "config"), XDG_DATA_HOME: join(root, "data"), XDG_STATE_HOME: join(root, "state"),
    XDG_CACHE_HOME: join(root, "cache"), HZ_CONFIG_HOME: join(root, "config"), HZ_DATA_DIR: join(root, "data"),
    HZ_STATE_DIR: join(root, "state"), HZ_CACHE_DIR: join(root, "cache"), HZ_LOG_DIR: join(root, "logs"),
    HZ_NATIVE_TEST_ROOT: root,
  };
  app = await electron.launch({
    executablePath: electronExecutable,
    args: [fileURLToPath(new URL("./real-backend-bootstrap.mjs", import.meta.url))],
    cwd: root,
    env,
    timeout: 45_000,
  });
  assert.equal(await app.evaluate(({ app: current }) => current.getPath("userData")), join(root, "electron-profile"));
  assert.equal(await app.evaluate(({ app: current }) => current.getPath("sessionData")), join(root, "electron-session"));
  const page = await app.firstWindow({ timeout: 45_000 });
  ownedPid = Number(await readFile(join(root, "owned-pid"), "utf8"));
  await waitForConnected(page);
  const first = await page.evaluate(() => window.heinzelDesktop.lifecycle());
  assert.ok(first.instance && first.version);
  const port = Number(await readFile(join(root, "owned-port"), "utf8"));
  assert.ok(Number.isSafeInteger(ownedPid) && ownedPid > 1);
  assert.ok(Number.isSafeInteger(port) && port > 0 && port <= 65535);
  const parent = spawnSync("/bin/ps", ["-o", "ppid=", "-p", String(ownedPid)], { encoding: "utf8" });
  assert.equal(Number(parent.stdout.trim()), app.process().pid, "The observed hz process must belong to this Electron instance.");
  await page.reload();
  await waitForConnected(page);
  const reloaded = await page.evaluate(() => window.heinzelDesktop.lifecycle());
  assert.equal(reloaded.instance, first.instance);
  assert.equal(reloaded.version, first.version);
  const exited = new Promise((resolve) => app.process().once("exit", (code, signal) => resolve({ code, signal })));
  await page.close();
  const result = await withTimeout(exited, 15_000, "Electron did not exit after close.");
  stopped = true;
  assert.equal(result.code, 0, `Electron closed abnormally: ${JSON.stringify(result)}`);
  assert.equal(result.signal, null);
  const ownedExit = JSON.parse(await readFile(join(root, "owned-exit"), "utf8"));
  assert.equal(await exists(join(root, "error-title")), false, `The desktop reported a non-graceful shutdown: ${JSON.stringify(ownedExit)}`);
  assert.deepEqual(ownedExit, { code: 0, signal: null }, "The owned CLI did not report a clean exit.");
  assert.equal(processExists(ownedPid), false, "The owned hz child remained alive after the window closed.");
  assert.equal(await listenerPresent(port), false, "The temporary loopback listener remained after the child exited.");
  console.log(`Isolated Electron opened and reloaded with installed hz ${first.version}; closing the window stopped its owned child.`);
} finally {
  if (app && !stopped) await app.close().catch(() => {});
  if (!ownedPid && await exists(join(root, "owned-pid"))) ownedPid = Number(await readFile(join(root, "owned-pid"), "utf8"));
  if (ownedPid && processExists(ownedPid)) throw new Error("The isolated owned child is still running; temporary state was retained for inspection.");
  await rm(root, { recursive: true, force: true });
}

async function withTimeout(promise, milliseconds, message) {
  let timer;
  try {
    return await Promise.race([promise, new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(message)), milliseconds); })]);
  } finally {
    clearTimeout(timer);
  }
}

async function waitForConnected(page) {
  try {
    await page.waitForFunction(() => document.querySelector("#status")?.textContent?.startsWith("Connected · version "), null, { timeout: 10_000 });
  } catch {
    const status = await page.locator("#status").textContent().catch(() => "unavailable");
    throw new Error(`The native window did not connect: ${status}`);
  }
}

async function exists(path) {
  try { await access(path); return true; } catch { return false; }
}
function processExists(pid) {
  try { process.kill(pid, 0); return true; } catch (error) { if (error.code === "ESRCH") return false; throw error; }
}
function listenerPresent(port) {
  return new Promise((resolve) => {
    const socket = connect({ host: "127.0.0.1", port });
    socket.setTimeout(1000);
    socket.once("connect", () => { socket.destroy(); resolve(true); });
    socket.once("error", () => resolve(false));
    socket.once("timeout", () => { socket.destroy(); resolve(true); });
  });
}
