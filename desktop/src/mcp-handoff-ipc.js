import { ownedLifecycleWindow } from "./lifecycle-ipc.js";

const actions = new Set(["start", "check", "stop", "install"]);
const idPattern = /^[a-f0-9]{32}(?![\s\S])/;
const refused = "The local MCP review plan is unavailable. Prepare it again.";

function commandFor(plan, id) {
  if (!plan || plan.id !== id || !actions.has(plan.action) ||
      typeof plan.registration !== "string" || !/^[A-Za-z0-9_-]{1,96}(?![\s\S])/.test(plan.registration) ||
      typeof plan.handoff !== "string" || !/^[1-9][0-9]{0,9}\.[a-f0-9]{64}(?![\s\S])/.test(plan.handoff)) {
    throw new Error(refused);
  }
  if (plan.session === "owning-session" && (plan.action === "check" || plan.action === "stop")) {
    return `${plan.action} --handoff ${plan.handoff}`;
  }
  if (plan.session !== "terminal" || plan.action === "stop") throw new Error(refused);
  const operation = plan.action === "install" ? "review-install" : plan.action;
  return `hz system mcp ${operation} ${plan.registration} --handoff ${plan.handoff}`;
}

/** Resolve only a locator on the owned backend. Native UI helps transfer it to the terminal; it cannot approve it. */
export function createMcpHandoffHandler({ prompt, copy, fetchPlan = fetch, ...guard }) {
  const pending = new WeakSet();
  return async (event, id) => {
    const { window, connection } = ownedLifecycleWindow(event, guard);
    if (typeof id !== "string" || !idPattern.test(id) || pending.has(window)) throw new Error(refused);
    pending.add(window);
    try {
      const response = await fetchPlan(`${connection.origin}/api/mcp/handoffs/${id}`, {
        headers: { Authorization: `Bearer ${connection.token}` }, redirect: "error", cache: "no-store",
        signal: AbortSignal.timeout(3000),
      });
      if (!response.ok) throw new Error(refused);
      const plan = await response.json();
      const command = commandFor(plan, id);
      if (ownedLifecycleWindow(event, guard).connection !== connection) throw new Error(refused);
      const result = await prompt(window, {
        type: "info",
        message: `Review ${plan.action} for ${plan.registration} in a local terminal`,
        detail: plan.session === "owning-session"
          ? `Enter this in the session that owns the MCP worker. That session will show the exact plan for approval.\n\n${command}`
          : `Run this on the host. The installed CLI will verify the plan and ask for approval.\n\n${command}`,
        buttons: ["Cancel", "Copy command"], cancelId: 0, defaultId: 0, noLink: true,
      });
      if (result.response !== 1) return false;
      if (ownedLifecycleWindow(event, guard).connection !== connection) throw new Error(refused);
      copy(command);
      return true;
    } finally {
      pending.delete(window);
    }
  };
}
