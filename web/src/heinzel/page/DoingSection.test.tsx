// "How it is doing": the state word hz computed and the counts of slice 5, nothing derived on the page.
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { components } from "@/api/schema";
import { sessionId } from "@/test/heinzelPage";
import { DoingSection } from "./DoingSection";

const summary = {
  session_id: sessionId,
  name: "Helmi",
  state: "awake",
  last_seq: 4,
  updated_at: "2026-11-14T09:00:00Z",
  waiting_count: 2,
  runs_last_hour: 3,
  queued_runs: 1,
  last_run_failed: false,
  last_approved_at: null,
  activity: "waiting_for_you",
} as components["schemas"]["SessionSummary"];

const row = (label: string) => screen.getByText(label).nextElementSibling;

describe("how it is doing", () => {
  it("shows the state word and the counts", () => {
    render(<DoingSection summary={summary} />);
    expect(screen.getByRole("heading", { name: "How it is doing" })).toBeInTheDocument();
    expect(screen.getByText("Waiting for you", { selector: "p" })).toBeInTheDocument();
    expect(row("Runs in the last hour")).toHaveTextContent("3");
    expect(row("Queued")).toHaveTextContent("1");
    expect(row("You last approved its work")).toHaveTextContent("Not yet");
    expect(screen.queryByText("Last run")).not.toBeInTheDocument();
  });

  it("says when the last run did not finish, with a mark beside the words", () => {
    render(<DoingSection summary={{ ...summary, last_run_failed: true, last_approved_at: "2026-11-14T08:00:00Z" }} />);
    expect(row("Last run")).toHaveTextContent("✖ Did not finish");
    expect(row("You last approved its work")).not.toHaveTextContent("Not yet");
  });

  it("shows nothing until the list has the Heinzel", () => {
    const { container } = render(<DoingSection summary={undefined} />);
    expect(container).toBeEmptyDOMElement();
  });
});
