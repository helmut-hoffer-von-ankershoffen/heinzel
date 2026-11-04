import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import review from "@/test/capturedWriteReview.json";
import { proposal } from "@/test/chat";
import { ActionCard } from "./Conversation";

describe("captured write confirmation", () => {
  it.each([
    ["document", "https://example.invalid/document?tab=chosen-tab", "Weekly notes"],
    ["spreadsheet", "https://example.invalid/spreadsheet", "Chosen sheet"],
    ["presentation", "https://example.invalid/presentation", "end"],
  ])("renders the insertion meaning and exact %s destination before confirmation", (_kind, reference, section) => {
    const onDecide = vi.fn();
    render(
      <ul>
        <ActionCard
          action={{
            ...proposal,
            module: "heinzel",
            operation: "execute-captured-write",
            command: review.command,
            arguments: {
              session_id: "fixture-session",
              request_id: "fixture-draft",
              review: {
                operation: "insert-draft",
                reference,
                section,
                content: "Fictional draft text for owner review",
                base_revision: "fixture-revision",
              },
            },
          }}
          busy={false}
          onDecide={onDecide}
        />
      </ul>,
    );
    expect(screen.getByText(review.command)).toBeVisible();
    expect(screen.queryByText(/full replacement/i)).not.toBeInTheDocument();
    const terms = screen.getByText(/"operation": "insert-draft"/);
    expect(terms).toHaveTextContent(reference);
    expect(terms).toHaveTextContent(section);
    expect(terms).toHaveTextContent("Fictional draft text for owner review");
    expect(terms).toHaveTextContent("fixture-revision");
    expect(screen.getByRole("button", { name: "Confirm" })).toBeEnabled();
    expect(onDecide).not.toHaveBeenCalled();
  });
});
