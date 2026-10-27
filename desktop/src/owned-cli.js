import { spawn as nodeSpawn } from "node:child_process";
import { parseConnection } from "./connection.js";

const STARTUP_TIMEOUT_MS = 30_000;
const STOP_TIMEOUT_MS = 5_000;
const FORCE_STOP_TIMEOUT_MS = 2_000;
const OUTPUT_LIMIT = 4096;

/** Start and own one foreground `hz ui` process for the lifetime of the Electron app. */
export function createOwnedCli({
  spawn = nodeSpawn,
  parse = parseConnection,
  startupTimeoutMs = STARTUP_TIMEOUT_MS,
  stopTimeoutMs = STOP_TIMEOUT_MS,
  forceStopTimeoutMs = FORCE_STOP_TIMEOUT_MS,
} = {}) {
  let child = null;
  let childClosed = false;
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
        if (onError) child?.removeListener("error", onError);
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
        child = spawn("hz", ["ui", "--port", "0", "--no-browser"], {
          shell: false,
          windowsHide: true,
          stdio: ["ignore", "pipe", "ignore"],
        });
      } catch {
        fail();
        return;
      }
      const ownedChild = child;
      ownedChild.once("close", () => { childClosed = true; });
      timer = setTimeout(() => fail(new Error("Heinzelmännchen took too long to start its local service.")), startupTimeoutMs);
      onError = () => fail();
      onExit = (code, signal) => {
        exitResult = Object.freeze({ code, signal });
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
      ownedChild.once("error", onError);
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
      let processError = false;
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

      if (!closed && !exited()) signal("SIGTERM");
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
    isRunning: () => Boolean(child) && child.exitCode === null && child.signalCode === null,
    exited: () => exitPromise,
    exitResult: () => exitResult,
  });
}
