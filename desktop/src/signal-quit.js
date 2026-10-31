const DEFAULT_SIGNALS = Object.freeze(["SIGTERM", "SIGINT", "SIGHUP"]);

/**
 * Turn a termination signal into the normal graceful quit so the owned service is stopped first.
 * If the quit has not finished by the deadline the app exits non-zero rather than hanging.
 */
export function installSignalQuit({
  proc,
  quit,
  forceExit,
  signals = DEFAULT_SIGNALS,
  deadlineMs = 15_000,
  setTimer = setTimeout,
}) {
  let received = null;
  const handler = (signal) => {
    if (received) return;
    received = signal;
    const timer = setTimer(() => forceExit(1), deadlineMs);
    timer?.unref?.();
    quit(signal);
  };
  for (const signal of signals) proc.on(signal, handler);
  return Object.freeze({ received: () => received });
}
