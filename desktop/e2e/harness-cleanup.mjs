/** Bound test teardown without signaling a PID discovered from the system process table. */
export async function withTimeout(promise, milliseconds, message) {
  let timer;
  try {
    return await Promise.race([
      promise,
      new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(message)), milliseconds); }),
    ]);
  } finally {
    clearTimeout(timer);
  }
}

/** Preserve temporary evidence whenever this test cannot establish that its owned child has exited. */
export async function cleanupHarness({ app, electronProcess, stopped, root, ownedPid, loadPid, processExists, remove, timeoutMs = 15_000 }) {
  if (app && !electronProcess)
    throw new Error(`The isolated Electron identity is unavailable; temporary evidence was retained at ${root}.`);
  if (app && !stopped) {
    try {
      await withTimeout(app.close(), timeoutMs, "The isolated Electron controller did not close.");
    } catch {
      // This ChildProcess belongs to the test; do not signal a PID from the process table.
      const process = electronProcess;
      if (process.exitCode === null && process.signalCode === null) {
        try { process.kill("SIGTERM"); } catch { /* Report uncertainty below. */ }
      }
      throw new Error(`The isolated Electron controller did not close; temporary evidence was retained at ${root}.`);
    }
  }
  if (app) {
    const process = electronProcess;
    if (process.exitCode === null && process.signalCode === null)
      throw new Error(`The isolated Electron controller is still running; temporary evidence was retained at ${root}.`);
  }
  if (!ownedPid && app) {
    try { ownedPid = await loadPid(); } catch {
      throw new Error(`The isolated child identity is unavailable; temporary evidence was retained at ${root}.`);
    }
  }
  if (app && (!Number.isSafeInteger(ownedPid) || ownedPid <= 1))
    throw new Error(`The isolated child identity is invalid; temporary evidence was retained at ${root}.`);
  if (ownedPid && processExists(ownedPid))
    throw new Error(`The isolated owned child is still running; temporary evidence was retained at ${root}.`);
  await remove(root);
}
