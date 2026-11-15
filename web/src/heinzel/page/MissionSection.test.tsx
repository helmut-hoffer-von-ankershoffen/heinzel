// The mission on the Heinzel's page: does alone, asks first, never, and the version of its terms.
import { screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { renderPage, sessionId, snapshot } from "@/test/heinzelPage";
import { mockApi } from "@/test/render";
import { MissionSection } from "./MissionSection";

const proposal = {
  session_id: sessionId,
  request_id: "terms-1",
  revision: 2,
  based_on_revision: 1,
  contract: "Keep the day in order.",
  max_usd: 0.5,
  timeout_seconds: 30,
  permitted_tools: [],
  status: "proposed",
  preview_sha256: "b".repeat(64),
};
const approved = {
  id: "terms",
  version: 3,
  sha256: "a".repeat(64),
  status: "approved",
  title: "Your day",
  text: "I keep your day in order.",
  created_at: "2026-11-10T00:00:00Z",
};

afterEach(() => sessionStorage.clear());

const group = (name: string) => within(screen.getByRole("list", { name }));

describe("mission", () => {
  it("names what it does alone, asks first and never does, and the approved version", async () => {
    mockApi({ "/api/heinzel/latest-scheduled-agent-proposal": proposal });
    renderPage(<MissionSection snapshot={{ ...snapshot, contract_version: 3, contracts: [approved] as never }} />);
    expect(screen.getByText("Your day")).toBeInTheDocument();
    expect(screen.getByText("Version 3")).toBeInTheDocument();
    expect(group("I do alone").getByText("Answer you in this chat")).toBeInTheDocument();
    expect(group("I do alone").getByText("Check in: every weekday at 08:00")).toBeInTheDocument();
    expect(group("I ask first").getByText("Before anything other people see")).toBeInTheDocument();
    expect(group("I never").getByText("Use a tool you did not approve")).toBeInTheDocument();
    expect(await group("I never").findByText("Spend more than $0.5 a turn")).toBeInTheDocument();
    expect(screen.getByText("I keep your day in order.")).toBeInTheDocument();
  });

  it("shows proposed terms as proposed, and no schedule line while nothing is on", async () => {
    mockApi({ "/api/heinzel/latest-scheduled-agent-proposal": { ...proposal, max_usd: null } });
    renderPage(<MissionSection snapshot={{ ...snapshot, schedule: null }} />);
    expect(await screen.findByText("Proposed · version 2")).toBeInTheDocument();
    expect(group("I do alone").queryByText(/Check in/)).not.toBeInTheDocument();
    expect(group("I never").queryByText(/Spend more/)).not.toBeInTheDocument();
    expect(screen.getByText("Keep the day in order.")).toBeInTheDocument();
  });

  it("asks for terms in chat when there are none", async () => {
    mockApi({ "/api/heinzel/latest-scheduled-agent-proposal": null });
    renderPage(<MissionSection snapshot={snapshot} />);
    expect(await screen.findByText("No terms yet. Ask Helmi in chat to propose them.")).toBeInTheDocument();
    expect(screen.queryByText("Read the full terms")).not.toBeInTheDocument();
  });

  it("refuses terms that belong to another Heinzel", async () => {
    mockApi({
      "/api/heinzel/latest-scheduled-agent-proposal": { ...proposal, session_id: "hz-01ARZ3NDEKTSV4RRFFQ69G5FAW" },
    });
    renderPage(<MissionSection snapshot={snapshot} />);
    expect(await screen.findByText("No terms yet. Ask Helmi in chat to propose them.")).toBeInTheDocument();
    expect(screen.queryByText("Keep the day in order.")).not.toBeInTheDocument();
  });
});
