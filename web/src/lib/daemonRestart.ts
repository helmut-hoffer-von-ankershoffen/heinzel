/** A successful transport does not prove that the daemon was installed or restarted. */
export function daemonRestartResult(
  result: unknown,
): "daemon.restarted" | "daemon.notRestarted" | "daemon.restartUnknown" {
  if (typeof result !== "object" || result === null || !("restarted" in result)) return "daemon.restartUnknown";
  if (result.restarted === true) return "daemon.restarted";
  if (result.restarted === false) return "daemon.notRestarted";
  return "daemon.restartUnknown";
}
