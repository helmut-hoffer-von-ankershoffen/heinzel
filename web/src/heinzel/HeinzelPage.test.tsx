// The Heinzel's page at /heinzel/:id: every section in its order, focus on the heading, and the reviews the chat's old
// Details held, now here.
import { screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { renderPage, sessionId, snapshot } from "@/test/heinzelPage";
import { expectAccessible, mockApi } from "@/test/render";
import { HeinzelPage } from "./HeinzelPage";

afterEach(() => sessionStorage.clear());

const sessions = {
  sessions: [
    {
      session_id: sessionId,
      name: "Helmi",
      state: "awake",
      last_seq: 1,
      updated_at: "2026-11-14T09:00:00Z",
      waiting_count: 0,
      runs_last_hour: 0,
      queued_runs: 0,
      last_run_failed: false,
      last_approved_at: null,
      activity: "proud",
    },
  ],
};

describe("the Heinzel's page", () => {
  it("shows its sections in order and moves focus to its heading", async () => {
    mockApi({ "/api/heinzel/session": snapshot, "/api/heinzel/sessions": sessions });
    const { container } = renderPage(<HeinzelPage />);
    const heading = await screen.findByRole("heading", { name: "Helmi, your Heinzel", level: 1 });
    await waitFor(() => expect(heading).toHaveFocus());
    expect(await screen.findByText("Done and proud", { selector: "h1 + p" })).toBeInTheDocument();
    const order = screen.getAllByRole("heading", { level: 2 }).map((item) => item.textContent);
    expect(order).toEqual([
      "Look and name",
      "Mission",
      "Schedule",
      "How it is doing",
      "Pause or retire",
      "Reviews and records",
    ]);
    // The old Details view is gone: no disclosure called Details, and no cron text or hash anywhere.
    expect(screen.queryByText("Details", { exact: true })).not.toBeInTheDocument();
    expect(container).not.toHaveTextContent("c".repeat(16));
    await expectAccessible(container);
  });

  it("reads the lifecycle word while the list has not answered", async () => {
    mockApi({ "/api/heinzel/session": { ...snapshot, state: "paused" } });
    renderPage(<HeinzelPage />);
    expect(await screen.findByText("Paused", { selector: "h1 + p" })).toBeInTheDocument();
  });

  it("says when the Heinzel cannot be loaded", async () => {
    mockApi({ "/api/heinzel/session": { status: 404, body: { error: "unknown" } } });
    renderPage(<HeinzelPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("This Heinzel could not be loaded.");
  });

  it("asks nothing for an address that is not a Heinzel", () => {
    const { calls } = mockApi({});
    renderPage(<HeinzelPage />, "/heinzel/not-a-heinzel");
    expect(screen.getByRole("alert")).toHaveTextContent("This Heinzel could not be loaded.");
    expect(calls).toHaveLength(0);
  });

  it("refuses a snapshot of another Heinzel", async () => {
    mockApi({ "/api/heinzel/session": { ...snapshot, session_id: "hz-01ARZ3NDEKTSV4RRFFQ69G5FAW" } });
    renderPage(<HeinzelPage />);
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });

  it("says Retired for a retired Heinzel and keeps only its records", async () => {
    mockApi({
      "/api/heinzel/session": { ...snapshot, state: "retired" },
      "/api/heinzel/sessions": {
        sessions: [{ ...sessions.sessions[0], state: "retired", activity: "waiting_for_you", waiting_count: 1 }],
      },
      "/api/heinzel/personal-artifacts": { session_id: sessionId, artifacts: [] },
    });
    renderPage(<HeinzelPage />);
    await screen.findByRole("heading", { level: 1 });
    await waitFor(() => expect(screen.getByText(/./, { selector: "h1 + p" })).toHaveTextContent("Retired"));
    expect(screen.getByRole("region", { name: "Records" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Reviews and records" })).not.toBeInTheDocument();
    expect(screen.queryByText("Scheduled Heinzel terms")).not.toBeInTheDocument();
  });
});
