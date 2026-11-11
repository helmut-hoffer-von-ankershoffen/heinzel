import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Phase } from "@/lib/operationRun";
import { ApplySteps, FULL_PLAN, NeedsAttention } from "./applySteps";
import { RestartApplyError } from "./controller";

function steps(plan = FULL_PLAN, restored?: Phase[]) {
  const reports: Phase[][] = [];
  let now = 1000;
  const subject = new ApplySteps((phases) => reports.push(phases), {}, restored, () => now, plan);
  return { subject, reports, tick: (ms: number) => (now += ms) };
}

describe("The steps of a change that restarts hz", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("lists every step of the plan as pending, in plan order", () => {
    const { subject } = steps(["preserve", "restart", "wait", "check"]);
    expect(subject.list().map((s) => [s.id, s.state])).toEqual([
      ["preserve", "pending"],
      ["restart", "pending"],
      ["wait", "pending"],
      ["check", "pending"],
    ]);
  });

  it("times a step from when it really starts to when it ends", () => {
    const { subject, tick } = steps();
    subject.mark("preserve", "running");
    tick(250);
    subject.mark("preserve", "done");
    const [preserve] = subject.list();
    expect(preserve).toMatchObject({ state: "done", startedAt: 1000, endedAt: 1250 });
  });

  it("does not invent an end time for a step that never ran", () => {
    const { subject } = steps();
    subject.mark("reload", "skipped", "Not needed");
    expect(subject.list().find((s) => s.id === "reload")).toMatchObject({ state: "skipped", detail: "Not needed" });
    expect(subject.list().find((s) => s.id === "reload")?.endedAt).toBeUndefined();
  });

  it("re-reports once a second while a step runs and stops when none does", () => {
    const { subject, reports } = steps();
    subject.mark("preserve", "running");
    const after = reports.length;
    vi.advanceTimersByTime(3000);
    expect(reports.length).toBe(after + 3);
    subject.mark("preserve", "done");
    const stopped = reports.length;
    vi.advanceTimersByTime(3000);
    expect(reports.length).toBe(stopped);
  });

  it("follows the restart lifecycle: the step it left is done, the one it entered runs", () => {
    const { subject } = steps();
    subject.onPhase("preserving");
    expect(subject.state("write")).toBe("running");
    subject.onPhase("restarting");
    expect(subject.state("write")).toBe("done");
    expect(subject.state("restart")).toBe("running");
    subject.onPhase("reconnecting");
    expect(subject.state("restart")).toBe("done");
    expect(subject.state("wait")).toBe("running");
    subject.onPhase("applied");
    expect(subject.state("wait")).toBe("done");
    subject.stop();
  });

  it("shows what the change's server reports as detail lines of the write", () => {
    const { subject } = steps();
    subject.heard({ id: "install", label: "Install", state: "running", detail: "Fetching" });
    expect(subject.list().find((s) => s.id === "write")).toMatchObject({ state: "running", detail: "Fetching" });
    subject.heard({ id: "needs:heinzel-x", label: "heinzel-x", state: "done", detail: "1.2" });
    subject.heard({ id: "verify", label: "Verify", state: "done" });
    expect(subject.list().find((s) => s.id === "write")?.detail).toBe("heinzel-x: 1.2 · Checked");
    subject.stop();
  });

  it("fails the write with the server's reason when a reported step fails", () => {
    const { subject } = steps();
    subject.heard({ id: "verify", label: "Verify", state: "failed", detail: "Hash mismatch" });
    expect(subject.list().find((s) => s.id === "write")).toMatchObject({ state: "failed", detail: "Hash mismatch" });
  });

  it("fails the running step with the error message", () => {
    const { subject } = steps();
    subject.mark("restart", "running");
    subject.failed(new Error("boom"));
    expect(subject.list().find((s) => s.id === "restart")).toMatchObject({ state: "failed", detail: "boom" });
  });

  it("marks a wait that ran out as needing attention, not as failed", () => {
    const { subject } = steps();
    subject.mark("wait", "running");
    subject.failed(new RestartApplyError("timeout", "no answer"));
    expect(subject.state("wait")).toBe("warn");
  });

  it("skips the restart steps of a change that needed no restart", () => {
    const { subject } = steps();
    subject.mark("write", "running");
    subject.noRestart();
    expect(subject.state("write")).toBe("done");
    for (const id of ["restart", "wait", "reload"] as const) expect(subject.state(id)).toBe("skipped");
    expect(subject.state("check")).toBe("pending");
  });

  it("restores the steps of an earlier attempt", () => {
    const { subject } = steps(FULL_PLAN, [{ id: "write", label: "Installing x", state: "done" }]);
    expect(subject.state("write")).toBe("done");
    expect(subject.list().find((s) => s.id === "write")?.label).toBe("Installing x");
  });

  it("is an error the person decides about when hz has not come back", () => {
    expect(new NeedsAttention("later").attention).toBe(true);
  });
});
