// One surface per write (owner 2026-11-08): a plugin change asks once, in the operation dialog, and shows its whole
// progress there. No inline confirm card, no second question about the restart the person already agreed to.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { expect, it } from "vitest";

const read = (path: string) => readFileSync(join(__dirname, "..", path), "utf8");
/** Every English message as [key, text], read from the catalogue's source. */
const messages = [...read("i18n.ts").matchAll(/^\s+"([\w.]+)":\s*"((?:[^"\\]|\\.)*)"/gm)].map(
  (match) => [match[1], match[2]] as const,
);

it("asks for a plugin change only in the operation dialog, never in an inline card or a second restart question", () => {
  const plugins = read("layout/PluginsSection.tsx");
  expect(plugins).not.toMatch(/RestartAndApply/);
  expect(plugins).not.toMatch(/<fieldset/);
  expect(plugins).toMatch(/requestDrivenOperation\(/);
});

it("never offers the person 'Restart and apply' for a change they already confirmed", () => {
  const offending = messages
    .filter(([key]) => key.startsWith("plugins.") || key.startsWith("op.") || key.startsWith("applySteps."))
    .filter(([, value]) => /restart and apply|restart the server to apply/i.test(String(value)));
  expect(offending).toEqual([]);
});

it("keeps the plan's step labels short and in the person's words", () => {
  const labels = messages.filter(([key]) => /^(applySteps|plugins\.(write|check))\./.test(key));
  for (const [key, value] of labels) {
    expect(String(value).length, key).toBeLessThanOrEqual(40);
    expect(String(value), key).not.toMatch(/\b(lifecycle|instance|verifying|applying)\b/i);
  }
});

it("gives every kind a plan that ends with its check, with wait and reload right after a restart", async () => {
  const { PLANS } = await import("./operationPlans");
  for (const [kind, plan] of Object.entries(PLANS)) {
    expect(plan.at(-1), kind).toBe("check");
    expect(plan[0], kind).toBe("preserve");
    const restart = plan.indexOf("restart" as never);
    if (restart >= 0) expect(plan.slice(restart, restart + 4), kind).toEqual(["restart", "wait", "reload", "check"]);
  }
});
