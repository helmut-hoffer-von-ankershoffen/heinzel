import { spawn as nodeSpawn } from "node:child_process";
import { accessSync, closeSync, constants, fstatSync, lstatSync, openSync, readSync, statSync } from "node:fs";
import { homedir } from "node:os";
import { isAbsolute, join } from "node:path";
import { parseConnection } from "./connection.js";

const STARTUP_TIMEOUT_MS = 30_000;
const STOP_TIMEOUT_MS = 5_000;
const FORCE_STOP_TIMEOUT_MS = 2_000;
const OUTPUT_LIMIT = 4096;

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

/** Start and own one foreground `hz ui` process for the lifetime of the Electron app. */
export function createOwnedCli({
  spawn = nodeSpawn,
  executable = () => "hz",
  parse = parseConnection,
  startupTimeoutMs = STARTUP_TIMEOUT_MS,
  stopTimeoutMs = STOP_TIMEOUT_MS,
  forceStopTimeoutMs = FORCE_STOP_TIMEOUT_MS,
} = {}) {
  let child = null;
  let childClosed = false;
  let childProcessError = false;
  let connection = null;
  let startPromise = null;
  let stopPromise = null;
  let resolveExit;
  let exitResult = null;
  const exitPromise = new Promise((resolve) => { resolveExit = resolve; });

  function start() {
    if (startPromise) return startPromise;
    startPromise = new Promise((resolve, reject) => {
      let settled = false;
      let output = "";
      let timer;
      let onData;
      let onError;
      let onExit;
      const cleanup = ({ preserveExit = false } = {}) => {
        clearTimeout(timer);
        if (onData) child?.stdout.removeListener("data", onData);
        if (!preserveExit && onExit) child?.removeListener("exit", onExit);
        output = "";
      };
      const fail = (error = new Error("Heinzelmännchen could not start its local service.")) => {
        if (settled) return;
        settled = true;
        if (!exitResult) {
          exitResult = Object.freeze({ code: null, signal: null, failedToStart: true });
          resolveExit(exitResult);
        }
        cleanup();
        reject(error);
      };
      try {
        childClosed = false;
        childProcessError = false;
        child = spawn(executable(), ["ui", "serve", "--port", "0", "--no-browser"], {
          shell: false,
          windowsHide: true,
          stdio: ["ignore", "pipe", "ignore"],
        });
      } catch {
        fail();
        return;
      }
      const ownedChild = child;
      ownedChild.once("close", () => {
        childClosed = true;
        if (onError) ownedChild.removeListener("error", onError);
      });
      timer = setTimeout(() => fail(new Error("Heinzelmännchen took too long to start its local service.")), startupTimeoutMs);
      onError = () => {
        childProcessError = true;
        if (!settled) {
          fail();
          return;
        }
        exitResult = Object.freeze({ code: null, signal: null, processError: true });
        resolveExit(exitResult);
      };
      onExit = (code, signal) => {
        exitResult = Object.freeze({ code, signal, ...(childProcessError ? { processError: true } : {}) });
        resolveExit(exitResult);
        if (!settled) fail();
      };
      onData = (chunk) => {
        if (settled) return;
        output += chunk.toString("utf8");
        if (output.length > OUTPUT_LIMIT) {
          fail();
          return;
        }
        const newline = output.indexOf("\n");
        if (newline < 0) return;
        const line = output.slice(0, newline).replace(/\r$/, "");
        if (!line || output.slice(newline + 1).trim()) {
          fail();
          return;
        }
        try {
          connection = parse([`--hz-url=${line}`]);
          settled = true;
          cleanup({ preserveExit: true });
          resolve(connection);
        } catch {
          fail();
        }
      };
      ownedChild.on("error", onError);
      ownedChild.once("exit", onExit);
      ownedChild.stdout.on("data", onData);
    });
    return startPromise;
  }

  function stop() {
    if (stopPromise) return stopPromise;
    const stopping = (async () => {
      const ownedChild = child;
      if (!ownedChild) return Object.freeze({ outcome: "not-started", code: null, signal: null, processError: false });

      const hasExitStatus = () => (ownedChild.exitCode !== null && ownedChild.exitCode !== undefined)
        || (ownedChild.signalCode !== null && ownedChild.signalCode !== undefined);
      const wasAlreadyExited = childClosed || hasExitStatus();
      if (wasAlreadyExited) {
        const priorExit = exitResult && !exitResult.failedToStart ? exitResult : null;
        return Object.freeze({
          outcome: "already-exited",
          code: priorExit?.code ?? ownedChild.exitCode,
          signal: priorExit?.signal ?? ownedChild.signalCode,
          processError: Boolean(priorExit?.processError),
        });
      }

      let closed = childClosed;
      let processError = childProcessError;
      let resolveClosed;
      const closedPromise = new Promise((resolve) => { resolveClosed = resolve; });
      // Node may emit an asynchronous ChildProcess `error` if a process exits between a status check
      // and kill(). Keep this listener until `close`, including after the bounded wait returns.
      const onError = () => { processError = true; };
      const onClose = () => {
        closed = true;
        childClosed = true;
        ownedChild.removeListener("error", onError);
        resolveClosed();
      };
      if (!closed) {
        ownedChild.on("error", onError);
        ownedChild.once("close", onClose);
      } else resolveClosed();

      const exited = hasExitStatus;
      const waitForClose = async (timeoutMs) => {
        if (closed) return true;
        let timer;
        const timedOut = new Promise((resolve) => { timer = setTimeout(() => resolve(false), timeoutMs); });
        const didClose = await Promise.race([closedPromise.then(() => true), timedOut]);
        clearTimeout(timer);
        return didClose;
      };
      const signal = (value) => {
        try {
          ownedChild.kill(value);
        } catch {
          processError = true;
          // The bounded wait below still allows the child to exit or receive the forced signal.
        }
      };

      if (!closed && !exited()) signal("SIGINT");
      if (!(await waitForClose(stopTimeoutMs))) {
        if (!closed && !exited()) {
          signal("SIGKILL");
        }
        if (!(await waitForClose(forceStopTimeoutMs))) {
          throw new Error("The local service did not stop after a forced shutdown.");
        }
      }

      const processExit = exitResult && !exitResult.failedToStart
        ? exitResult
        : { code: ownedChild.exitCode, signal: ownedChild.signalCode };
      const code = processExit.code ?? null;
      const signalCode = processExit.signal ?? null;
      const outcome = signalCode === "SIGKILL"
        ? "forced"
        : !processError && code === 0 && signalCode === null
          ? "graceful"
          : "abnormal";
      const result = Object.freeze({ outcome, code, signal: signalCode, processError });
      exitResult = Object.freeze({ code, signal: signalCode, processError });
      return result;
    })();
    stopPromise = stopping.then((result) => {
      // An abnormal exit has stopped the child, but the caller can retry once to close the app
      // after reporting it. The retry returns `already-exited`, never a false graceful result.
      if (result.outcome === "abnormal") stopPromise = null;
      return result;
    }).catch((error) => {
      stopPromise = null;
      throw error;
    });
    return stopPromise;
  }

  return Object.freeze({
    start,
    stop,
    currentConnection: () => connection,
    isRunning: () => Boolean(child) && !childClosed && !childProcessError && child.exitCode === null && child.signalCode === null,
    exited: () => exitPromise,
    exitResult: () => exitResult,
  });
}
