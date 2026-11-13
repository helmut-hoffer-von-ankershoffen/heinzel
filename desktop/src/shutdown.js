/** Stop the owned service and tell the caller about an observed unclean exit before the shell exits. */
export async function stopWithReport(ownedCli, report) {
  const result = await ownedCli.stop();
  const unclean = result.outcome === "forced" || result.outcome === "abnormal" || (
    result.outcome === "already-exited" && (
      (result.code !== null && result.code !== 0) || result.signal !== null || result.processError
    )
  );
  if (unclean) report();
  return result;
}

/** Refresh only after a healthy packaged macOS session shuts down normally. */
export function shouldRefresh({ packaged, platform, fixture, serviceStarted, unexpectedExit, signal, outcome }) {
  return packaged && platform === "darwin" && !fixture && serviceStarted && !unexpectedExit && !signal && outcome === "graceful";
}

/** Release the installation lease before requesting a detached, bounded CLI refresh. */
export async function refreshAfterQuit({ eligible, gate, executable, spawn, timeoutMs = 5000 }) {
  if (!eligible || !gate) return false;
  let command;
  try { command = executable(); } catch { return false; }
  const closed = new Promise((resolve) => {
    let timer;
    const finish = (value) => { clearTimeout(timer); resolve(value); };
    gate.once("close", () => finish(true));
    timer = setTimeout(() => finish(false), timeoutMs);
    gate.stdin.end();
  });
  if (!(await closed)) return false;
  try {
    const child = spawn(command, ["app", "refresh"], { detached: true, stdio: "ignore", shell: false });
    child.on("error", () => {});
    child.unref();
    return true;
  } catch { return false; }
}

/** Hide the shell's windows while a quit stops the service; returns the ones it hid, to show again if the quit fails. */
export function hideWindows(windows) {
  const hidden = [];
  for (const window of windows) {
    try {
      if (window.isDestroyed() || !window.isVisible()) continue;
      window.hide();
      hidden.push(window);
    } catch {
      // A window that went away while quitting needs no hiding.
    }
  }
  return hidden;
}

/** Show the windows a failed quit hid, so Quit can be tried again. */
export function showWindows(windows) {
  for (const window of windows) {
    try {
      if (!window.isDestroyed()) window.show();
    } catch {
      // A window that went away stays gone.
    }
  }
}

/** One log line for a service stop during a deliberate quit that did not end with a clean exit. */
export function quitLogLine({ outcome, code = null, signal = null } = {}) {
  return JSON.stringify({ event: "desktop.quit.service_stop", outcome, code, signal });
}
