import { describe, expect, it } from "vitest";
import { daemonRestartResult } from "./daemonRestart";

describe("The daemon restart result", () => {
  it("says restarted only when the daemon reports it restarted", () => {
    expect(daemonRestartResult({ restarted: true })).toBe("daemon.restarted");
  });

  it("says not restarted when the daemon reports it did not", () => {
    expect(daemonRestartResult({ restarted: false })).toBe("daemon.notRestarted");
  });

  it("does not claim a restart from a successful transport alone", () => {
    for (const result of [undefined, null, "ok", 1, {}, { restarted: "true" }, { restarted: 1 }, { restarted: null }]) {
      expect(daemonRestartResult(result)).toBe("daemon.restartUnknown");
    }
  });
});
