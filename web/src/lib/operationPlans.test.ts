import { describe, expect, it } from "vitest";
import { FULL_PLAN } from "@/lifecycle/applySteps";
import { PLANS } from "./operationPlans";

describe("The step plan of every restarting write", () => {
  const plans = Object.entries(PLANS) as [string, readonly string[]][];

  it("starts by saving open work and ends with the final check", () => {
    for (const [kind, plan] of plans) {
      expect(plan[0], kind).toBe("preserve");
      expect(plan.at(-1), kind).toBe("check");
    }
  });

  it("waits for hz and reloads after every restart, in that order", () => {
    for (const [kind, plan] of plans) {
      const restart = plan.indexOf("restart");
      expect(restart, kind).toBeGreaterThan(-1);
      expect(plan.slice(restart), kind).toEqual(["restart", "wait", "reload", "check"]);
    }
  });

  it("only names steps of the full plan and never repeats one", () => {
    for (const [kind, plan] of plans) {
      expect(new Set(plan).size, kind).toBe(plan.length);
      for (const step of plan) expect(FULL_PLAN, `${kind} ${step}`).toContain(step);
    }
  });

  it("keeps the order of the full plan", () => {
    for (const [kind, plan] of plans) {
      const positions = plan.map((step) => FULL_PLAN.indexOf(step as (typeof FULL_PLAN)[number]));
      expect(positions, kind).toEqual([...positions].sort((a, b) => a - b));
    }
  });

  it("writes only for the kinds that change something", () => {
    const writes = plans.filter(([, plan]) => plan.includes("write")).map(([kind]) => kind);
    expect(writes).toEqual([
      "plugin.install",
      "plugin.remove",
      "plugin.enable",
      "plugin.disable",
      "ai.client.install",
      "setting.apply",
    ]);
  });
});
