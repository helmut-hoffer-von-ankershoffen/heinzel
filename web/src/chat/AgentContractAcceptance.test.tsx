import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it } from "vitest";
import type { SchemaScheduledAgentProposal } from "@/api/client";
import { mockApi } from "@/test/render";
import { AgentContractAcceptance } from "./AgentContractAcceptance";

const SESSION = "durable-1";
const proposal = {
  session_id: SESSION,
  request_id: "req-1",
  title: "Morning digest",
  schedule: { at: "07:00", days: ["mon", "tue"] },
} as unknown as SchemaScheduledAgentProposal;
// The same terms with the keys in another order: acceptance compares the content, not the key order.
const reordered = { schedule: { days: ["mon", "tue"], at: "07:00" }, title: "Morning digest", ...proposal };

const action = (status: string, extra: Record<string, unknown> = {}) => ({
  id: "act-1",
  module: "heinzel",
  operation: "accept-agent-contract",
  command: "hz heinzel accept-agent-contract",
  status,
  arguments: { session_id: SESSION, request_id: "req-1", review: proposal },
  ...extra,
});
const chatWith = (actions: unknown[], extra: Record<string, unknown> = {}) => ({
  id: "chat-1",
  personal_session_id: SESSION,
  actions,
  messages: [],
  ...extra,
});

function mount(path = "/") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <AgentContractAcceptance sessionId={SESSION} proposal={proposal} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const posts = (calls: Request[]) => calls.filter((c) => c.method === "POST").map((c) => new URL(c.url).pathname);

describe("Accepting the terms of a scheduled agent", () => {
  beforeEach(() => sessionStorage.setItem("hz.token", "test-token"));

  it("always says that accepting grants nothing", async () => {
    mockApi({ "/api/heinzel/agent-contract-review": { proposal, accepted: false } });
    mount();
    expect(screen.getByText(/does not activate a schedule or grant access or tools/)).toBeInTheDocument();
  });

  it("offers to review terms the server confirms, whatever the order of their keys", async () => {
    mockApi({ "/api/heinzel/agent-contract-review": { proposal: reordered, accepted: false } });
    mount();
    await waitFor(() => expect(screen.getByRole("button", { name: "Review these terms" })).toBeEnabled());
  });

  it("refuses terms that differ from what the server holds", async () => {
    mockApi({
      "/api/heinzel/agent-contract-review": { proposal: { ...proposal, title: "Something else" }, accepted: false },
    });
    mount();
    expect(await screen.findByRole("alert")).toHaveTextContent("These terms could not be verified");
    expect(screen.getByRole("button", { name: "Review these terms" })).toBeDisabled();
  });

  it("says terms were accepted and offers no review", async () => {
    mockApi({ "/api/heinzel/agent-contract-review": { proposal, accepted: true } });
    mount();
    expect(await screen.findByRole("status")).toHaveTextContent("These exact terms were accepted.");
    expect(screen.queryByRole("button", { name: "Review these terms" })).not.toBeInTheDocument();
  });

  it("stages a review conversation for the exact request and no more", async () => {
    const user = userEvent.setup();
    const { calls } = mockApi({
      "/api/heinzel/agent-contract-review": { proposal, accepted: false },
      "/api/system/runner/chat/propose-agent-contract": chatWith([action("pending")]),
    });
    mount();
    const button = await screen.findByRole("button", { name: "Review these terms" });
    await waitFor(() => expect(button).toBeEnabled());
    await user.click(button);
    await waitFor(() => expect(posts(calls)).toEqual(["/api/system/runner/chat/propose-agent-contract"]));
    const staged = calls.find((c) => c.method === "POST");
    const query = new URL(staged?.url ?? "").searchParams;
    expect(query.get("durable_session")).toBe(SESSION);
    expect(query.get("request_id")).toBe("req-1");
  });

  it("shows a failure to stage without confirming anything", async () => {
    const user = userEvent.setup();
    const { calls } = mockApi({
      "/api/heinzel/agent-contract-review": { proposal, accepted: false },
      "/api/system/runner/chat/propose-agent-contract": { status: 500, body: { error: "no" } },
    });
    mount();
    const button = await screen.findByRole("button", { name: "Review these terms" });
    await waitFor(() => expect(button).toBeEnabled());
    await user.click(button);
    expect(await screen.findByText(/could not be confirmed/)).toBeInTheDocument();
    expect(posts(calls).some((path) => path.endsWith("/confirm"))).toBe(false);
  });

  it("confirms a pending action only after reading the same terms again", async () => {
    const user = userEvent.setup();
    const { calls } = mockApi({
      "/api/heinzel/agent-contract-review": { proposal, accepted: false },
      "/api/system/runner/chat/show": chatWith([action("pending")]),
      "/api/system/runner/chat/confirm": chatWith([action("done")]),
    });
    mount("/?contract-chat=chat-1");
    await user.click(await screen.findByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(posts(calls)).toContain("/api/system/runner/chat/confirm"));
    const paths = calls.map((c) => `${c.method} ${new URL(c.url).pathname}`);
    const reread = paths.lastIndexOf(
      "GET /api/heinzel/agent-contract-review",
      paths.indexOf("POST /api/system/runner/chat/confirm"),
    );
    expect(reread).toBeGreaterThan(-1);
    expect(new URL(calls.find((c) => c.method === "POST")?.url ?? "").searchParams.get("action")).toBe("act-1");
  });

  it("does not confirm when the terms changed since they were shown", async () => {
    const user = userEvent.setup();
    let reads = 0;
    const { calls } = mockApi({
      "/api/heinzel/agent-contract-review": () => ({
        proposal: ++reads === 1 ? proposal : { ...proposal, title: "Changed" },
        accepted: false,
      }),
      "/api/system/runner/chat/show": chatWith([action("pending")]),
      "/api/system/runner/chat/confirm": chatWith([action("done")]),
    });
    mount("/?contract-chat=chat-1");
    await user.click(await screen.findByRole("button", { name: "Confirm" }));
    expect(await screen.findByText(/could not be confirmed/)).toBeInTheDocument();
    expect(posts(calls)).not.toContain("/api/system/runner/chat/confirm");
  });

  it("declines without reading the terms again", async () => {
    const user = userEvent.setup();
    const { calls } = mockApi({
      "/api/heinzel/agent-contract-review": { proposal, accepted: false },
      "/api/system/runner/chat/show": chatWith([action("pending")]),
      "/api/system/runner/chat/decline": chatWith([action("declined")]),
    });
    mount("/?contract-chat=chat-1");
    await user.click(await screen.findByRole("button", { name: "Decline" }));
    await waitFor(() => expect(posts(calls)).toEqual(["/api/system/runner/chat/decline"]));
    expect(await screen.findByText(/These terms were declined/)).toBeInTheDocument();
  });

  it("offers only a check of the outcome for an interrupted confirmation", async () => {
    const user = userEvent.setup();
    const { calls } = mockApi({
      "/api/heinzel/agent-contract-review": { proposal, accepted: false },
      "/api/system/runner/chat/show": chatWith([action("running")]),
      "/api/system/runner/chat/recover": chatWith([action("done")]),
    });
    mount("/?contract-chat=chat-1");
    expect(screen.queryByRole("button", { name: "Confirm" })).not.toBeInTheDocument();
    await user.click(await screen.findByRole("button", { name: "Check outcome" }));
    await waitFor(() => expect(posts(calls)).toEqual(["/api/system/runner/chat/recover"]));
  });

  it("says an uncertain outcome will not be repeated", async () => {
    mockApi({
      "/api/heinzel/agent-contract-review": { proposal, accepted: false },
      "/api/system/runner/chat/show": chatWith([action("uncertain")]),
    });
    mount("/?contract-chat=chat-1");
    expect(await screen.findByText(/will not be repeated/)).toBeInTheDocument();
  });

  it("ignores an action whose arguments carry an approval proof or other terms", async () => {
    const { calls } = mockApi({
      "/api/heinzel/agent-contract-review": { proposal, accepted: false },
      "/api/system/runner/chat/show": chatWith([
        action("pending", {
          arguments: { session_id: SESSION, request_id: "req-1", review: proposal, approval_proof: "x" },
        }),
        action("pending", {
          id: "act-2",
          arguments: { session_id: SESSION, request_id: "req-1", review: { ...proposal, title: "Other" } },
        }),
      ]),
    });
    mount("/?contract-chat=chat-1");
    await waitFor(() => expect(calls.some((c) => c.url.includes("/chat/show"))).toBe(true));
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(screen.queryByRole("button", { name: "Confirm" })).not.toBeInTheDocument();
    expect(screen.queryByText("Review Heinzel terms")).not.toBeInTheDocument();
  });

  it("does not trust a conversation of another session", async () => {
    mockApi({
      "/api/heinzel/agent-contract-review": { proposal, accepted: false },
      "/api/system/runner/chat/show": chatWith([action("pending")], { personal_session_id: "someone-else" }),
    });
    mount("/?contract-chat=chat-1");
    expect(await screen.findByText(/saved review conversation could not be verified/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Confirm" })).not.toBeInTheDocument();
  });
});
