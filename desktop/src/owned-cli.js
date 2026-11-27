import { spawn as nodeSpawn } from "node:child_process";
import { accessSync, closeSync, constants, fstatSync, lstatSync, openSync, readSync, statSync } from "node:fs";
import { homedir } from "node:os";
import { isAbsolute, join } from "node:path";
import { parseConnection } from "./connection.js";

// Installing the service on first launch plus its bounded readiness wait take well under this.
const STARTUP_TIMEOUT_MS = 60_000;
const OUTPUT_LIMIT = 4096;
// How often the app asks the attached service whether it still answers; a lost service is attached again.
const PROBE_INTERVAL_MS = 5_000;
const LOST_AFTER = 3;
/** The exact arguments: the app only ever attaches, never `ui serve`. */
export const ATTACH_ARGUMENTS = Object.freeze(["ui", "attach", "--bridge-token-stdin"]);

/** Read the installer-managed binding; never fall back to PATH in an installed app. */
export function installedCli(home = homedir()) {
  const parents = [home, join(home, ".config"), join(home, ".config", "heinzel")];
  const receipt = join(parents[2], "desktop-owned-cli.json");
  let fd;
  try {
    const uid = process.getuid();
    const inspectParents = () => parents.map((parent) => {
      const info = lstatSync(parent);
      if (!info.isDirectory() || info.uid !== uid || (info.mode & 0o022)) throw new Error();
      return info;
    });
    const same = (a, b) => a.dev === b.dev && a.ino === b.ino && a.mode === b.mode &&
      a.uid === b.uid && a.nlink === b.nlink && a.size === b.size &&
      a.mtimeMs === b.mtimeMs && a.ctimeMs === b.ctimeMs;
    const beforeParents = inspectParents();
    fd = openSync(receipt, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
    const info = fstatSync(fd);
    if (!info.isFile() || info.uid !== uid || info.nlink !== 1 || (info.mode & 0o077) || info.size > 4096)
      throw new Error();
    const bytes = Buffer.alloc(4097);
    let length = 0;
    while (length < bytes.length) {
      const count = readSync(fd, bytes, length, bytes.length - length, null);
      if (!count) break;
      length += count;
    }
    const afterParents = inspectParents();
    if (length !== info.size || length > 4096 || !same(info, fstatSync(fd)) ||
        !same(info, lstatSync(receipt)) || beforeParents.some((parent, i) => !same(parent, afterParents[i])))
      throw new Error();
    const data = JSON.parse(bytes.subarray(0, length).toString("utf8"));
    if (!data || Array.isArray(data) || Object.keys(data).length !== 2 ||
        !Object.hasOwn(data, "executable") || !Object.hasOwn(data, "version") ||
        data.version !== 1 || typeof data.executable !== "string" || !isAbsolute(data.executable) ||
        data.executable.includes("\0")) throw new Error();
    if (!statSync(data.executable).isFile()) throw new Error();
    accessSync(data.executable, constants.X_OK);
    return data.executable;
  } catch {
    throw new Error("Heinzelmännchen could not find its installed local service.");
  } finally {
    if (fd !== undefined) closeSync(fd);
  }
}

/**
 * The CLI an unpackaged (dev) app attaches with: `HZ_DESKTOP_DEV_CLI` when it is an absolute path, else `hz` on
 * PATH. `hz ui attach` installs or starts that user's own login service, so point this at an isolated stand-in
 * (`e2e/isolated-service.mjs`, as the e2e harnesses do) to keep a dev run off your own service.
 */
export function developmentCli(env = process.env) {
  const chosen = env.HZ_DESKTOP_DEV_CLI;
  return typeof chosen === "string" && isAbsolute(chosen) && !chosen.includes("\0") ? chosen : "hz";
}

/** Hold the installer's exclusive gate until this process exits. */
export function acquireDesktopGate(home = homedir(), { spawn = nodeSpawn, timeoutMs = 5000 } = {}) {
  const parents = [home, join(home, ".config"), join(home, ".config", "heinzel")];
  const lock = join(parents[2], "desktop-use.lock");
  const journal = join(parents[2], "desktop-installation-journal.json");
  let fd;
  let child;
  try {
    const uid = process.getuid();
    const safeParents = () => parents.map((name) => {
      const info = lstatSync(name);
      if (!info.isDirectory() || info.uid !== uid || (info.mode & 0o022)) throw new Error();
      return info;
    });
    const identity = (a, b) => a.dev === b.dev && a.ino === b.ino && a.uid === b.uid && a.mode === b.mode && a.nlink === b.nlink;
    const before = safeParents();
    fd = openSync(lock, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
    const info = fstatSync(fd);
    if (!info.isFile() || info.uid !== uid || info.nlink !== 1 || (info.mode & 0o077)) throw new Error();
    child = spawn("/usr/bin/lockf", ["-t", "0", "/dev/fd/3", "/bin/sh", "-c", "printf 'ready\\n'; exec /bin/cat"], {
      stdio: ["pipe", "pipe", "ignore", fd], shell: false,
    });
    closeSync(fd);
    fd = undefined;
    return new Promise((resolve, reject) => {
      let settled = false;
      let output = "";
      const fail = () => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        child.stdin.destroy();
        child.kill();
        reject(new Error("Heinzelmännchen could not secure its installation."));
      };
      const timer = setTimeout(fail, timeoutMs);
      child.once("error", fail);
      child.once("close", fail);
      child.stdout.on("data", (data) => {
        if (settled) return;
        output += data.toString("utf8");
        if (output !== "ready\n") { if (!"ready\n".startsWith(output)) fail(); return; }
        try {
          const after = safeParents();
          if (!identity(info, lstatSync(lock)) || before.some((parent, i) => !identity(parent, after[i]))) throw new Error();
          try { lstatSync(journal); throw new Error(); } catch (error) { if (error.code !== "ENOENT") throw error; }
          settled = true;
          clearTimeout(timer);
          resolve(child);
        } catch { fail(); }
      });
    });
  } catch {
    if (fd !== undefined) closeSync(fd);
    child?.kill();
    return Promise.reject(new Error("Heinzelmännchen could not secure its installation."));
  }
}

/**
 * Attach to this computer's one owned web service through `hz ui attach`; never start a server of its own.
 *
 * `hz ui attach` installs or starts the supervised service when needed, prints one session link and exits 0. The
 * browser-bridge token goes to it on stdin, never in arguments or this process's environment, and it registers the
 * token with the live service. The app keeps no hz process: Quit, a crash or an update leaves the service running,
 * so no second server with its own auto-updater on old code can exist.
 */
export function createOwnedCli({
  spawn = nodeSpawn,
  executable = () => "hz",
  parse = parseConnection,
  // Extra variables for the attach command only; never added to this process's own.
  env = {},
  bridgeToken = null,
  // Asks the attached service whether it still answers this connection (its lifecycle); rejects when it does not.
  probe = null,
  probeContext = () => 0,
  probeIntervalMs = PROBE_INTERVAL_MS,
  // Consecutive failed probes before the service counts as lost: one slow answer during a restart is not a loss.
  lostAfter = LOST_AFTER,
  startupTimeoutMs = STARTUP_TIMEOUT_MS,
  setInterval: every = setInterval,
  clearInterval: stopEvery = clearInterval,
} = {}) {
  let connection = null;
  let startPromise = null;
  let attached = false;
  let detached = false;
  let failed = false;
  let resolveExit;
  let exitResult = null;
  let watcher = null;
  // Settles when attaching failed, or when the attached service stops answering this connection (a crash restart
  // has a new session token, and forgets the bridge token): the app then attaches again.
  const exitPromise = new Promise((resolve) => { resolveExit = resolve; });

  function start() {
    if (startPromise) return startPromise;
    startPromise = new Promise((resolve, reject) => {
      let settled = false;
      let output = "";
      let timer;
      let child;
      const fail = (error = new Error("Heinzelmännchen could not reach its local service.")) => {
        if (settled) return;
        settled = true;
        failed = true;
        clearTimeout(timer);
        exitResult = Object.freeze({ code: null, signal: null, failedToStart: true });
        resolveExit(exitResult);
        try { if (child && child.exitCode === null && child.signalCode === null) child.kill("SIGINT"); } catch { /* gone */ }
        reject(error);
      };
      try {
        child = spawn(executable(), ATTACH_ARGUMENTS, {
          shell: false,
          windowsHide: true,
          stdio: ["pipe", "pipe", "ignore"],
          ...(Object.keys(env).length ? { env: { ...process.env, ...env } } : {}),
        });
      } catch {
        fail();
        return;
      }
      timer = setTimeout(() => fail(new Error("Heinzelmännchen took too long to reach its local service.")), startupTimeoutMs);
      child.on("error", () => fail());
      child.stdout.on("data", (chunk) => {
        if (settled) return;
        output += chunk.toString("utf8");
        if (output.length > OUTPUT_LIMIT) fail();
      });
      child.once("close", (code, signal) => {
        if (settled) return;
        const lines = output.split("\n");
        const line = (lines[0] ?? "").replace(/\r$/, "");
        output = "";
        if (code === 2 && !line) {
          // An older hz has no `ui attach`: Typer's usage error. Say what fixes it instead of failing silently.
          fail(new Error("This hz is too old for the app. Run `hz system update`, then open the app again."));
          return;
        }
        if (code !== 0 || signal !== null || lines.length !== 2 || lines[1] !== "" || !line) {
          fail();
          return;
        }
        try {
          connection = parse([`--hz-url=${line}`]);
        } catch {
          fail();
          return;
        }
        settled = true;
        attached = true;
        clearTimeout(timer);
        if (probe) watch();
        resolve(connection);
      });
      // An attach that exits before reading its input must not crash the main process with EPIPE.
      child.stdin?.on("error", () => {});
      try {
        child.stdin.end(bridgeToken ? `${bridgeToken}\n` : "\n");
      } catch {
        fail();
      }
    });
    return startPromise;
  }

  function watch() {
    let probing = false;
    let failures = 0, previousContext;
    watcher = every(async () => {
      const context = probeContext();
      if (context !== previousContext) failures = 0;
      previousContext = context;
      if (context === null || probing || !attached || detached) return;
      probing = true;
      try {
        await probe(connection);
        failures = 0;
      } catch (error) {
        if (error?.code === "SESSION_CHANGED") { failures = 0; return; }
        if (context !== probeContext()) return;
        failures += 1;
        if (!attached || detached || failures < lostAfter) return;
        attached = false;
        stopEvery(watcher);
        watcher = null;
        exitResult = Object.freeze({ code: null, signal: null, serviceLost: true });
        resolveExit(exitResult);
      } finally { probing = false; }
    }, probeIntervalMs);
    watcher?.unref?.();
  }

  /** Leave the service running: it belongs to the service manager, not to this app. */
  async function stop() {
    if (watcher) { stopEvery(watcher); watcher = null; }
    if (!startPromise || failed) return Object.freeze({ outcome: "not-started", code: null, signal: null, processError: false });
    await startPromise.catch(() => {});
    detached = true;
    return Object.freeze({ outcome: "detached", code: null, signal: null, processError: false });
  }

  return Object.freeze({
    start,
    stop,
    currentConnection: () => connection,
    isRunning: () => attached && !detached,
    exited: () => exitPromise,
    exitResult: () => exitResult,
  });
}
