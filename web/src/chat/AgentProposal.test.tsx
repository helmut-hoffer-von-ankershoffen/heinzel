import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { mockApi } from "@/test/render";
import { AgentProposal } from "./AgentProposal";

const sessionId = "hz-01ARZ3NDEKTSV4RRFFQ69G5FAV";

describe("scheduled agent proposal", () => {
  it("creates and amends exact terms while showing they are not approved or running", async () => {
    let current: Record<string, unknown> | null = null;
    const save = async (request: Request) => {
      const body = await request.clone().json();
      current = {
        ...body,
        revision: current ? 2 : 1,
        based_on_revision: current ? 1 : 0,
        permitted_tools: [],
        status: "proposed",
        preview_sha256: "a".repeat(64),
      };
      return current;
    };
    const { calls } = mockApi({
      "/api/heinzel/latest-scheduled-agent-proposal": () => current,
      "/api/heinzel/propose-scheduled-agent": save,
      "/api/heinzel/amend-scheduled-agent": save,
    });
    const user = userEvent.setup();
    render(<AgentProposal sessionId={sessionId} />);
    expect(await screen.findByText(/They do not start an agent or grant approval/)).toBeInTheDocument();
    await user.type(await screen.findByLabelText("Instructions"), "Review the project");
    await user.click(screen.getByRole("button", { name: "Propose terms" }));
    expect(await screen.findByText("Revision 1 · Proposed")).toBeInTheDocument();
    const input = screen.getByLabelText("Instructions");
    await user.clear(input);
    await user.type(input, "Review recent changes");
    await user.click(screen.getByRole("button", { name: "Propose revised terms" }));
    expect(await screen.findByText("Revision 2 · Proposed")).toBeInTheDocument();
    const amended = calls.find((request) => new URL(request.url).pathname.endsWith("/amend-scheduled-agent"));
    expect(await amended?.clone().json()).toMatchObject({
      session_id: sessionId,
      expected_revision: 1,
      contract: "Review recent changes",
    });
    await waitFor(() => expect(screen.getByText(/limits are not enforced/)).toBeInTheDocument());
  });
});

it("recovers a lost acknowledgement after reload by retrying the same request", async () => {
  let stored: Record<string, unknown> | null = null;
  let writes = 0;
  const { calls } = mockApi({
    "/api/heinzel/latest-scheduled-agent-proposal": () => stored,
    "/api/heinzel/propose-scheduled-agent": async (request: Request) => {
      const body = await request.clone().json();
      writes += 1;
      stored ??= {
        ...body,
        revision: 1,
        based_on_revision: 0,
        permitted_tools: [],
        status: "proposed",
        preview_sha256: "b".repeat(64),
      };
      return writes === 1 ? { status: 503, body: { error: "unknown" } } : stored;
    },
  });
  const user = userEvent.setup();
  const first = render(<AgentProposal sessionId={sessionId} />);
  await user.type(await screen.findByLabelText("Instructions"), "Inspect the report");
  await user.click(screen.getByRole("button", { name: "Propose terms" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Could not confirm");
  first.unmount();
  render(<AgentProposal sessionId={sessionId} />);
  await user.click(await screen.findByRole("button", { name: "Retry same proposal" }));
  await waitFor(() => expect(sessionStorage.getItem(`hz.agent-proposal.pending.${sessionId}`)).toBeNull());
  const attempts = calls.filter((request) => new URL(request.url).pathname.endsWith("/propose-scheduled-agent"));
  expect(attempts).toHaveLength(2);
  const [firstAttempt, secondAttempt] = attempts;
  if (!firstAttempt || !secondAttempt) throw new Error("Expected both attempts");
  expect(await firstAttempt.clone().json()).toEqual(await secondAttempt.clone().json());
  expect(screen.getByText("Revision 1 · Proposed")).toBeInTheDocument();
});
