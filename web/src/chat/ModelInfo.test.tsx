import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { msg } from "@/test/chat";
import { expectAccessible } from "@/test/render";
import { costLabel, MessageInfo } from "./MessageInfo";

describe("An answer's details", () => {
  const answer = msg({
    role: "assistant",
    text: "Hi!",
    runner: "claude-code",
    access: "claude-code",
    model: "claude-opus-5-5",
    effort: "medium",
    billing: "plan",
    duration_seconds: 3.24,
    input_tokens: 2,
    output_tokens: 120,
    cache_read_tokens: 15000,
    cache_write_tokens: 800,
    cost_usd: 0.1337,
  });

  it("labels a subscription's cost as plan usage at API prices, and an API key's as spend", () => {
    expect(costLabel(answer)).toBe("uses your plan · ≈ $0.1337 at API prices");
    expect(costLabel({ ...answer, billing: "api" })).toBe("$0.1337");
    expect(costLabel({ ...answer, cost_usd: null, budget_usd: 0.25 })).toBe("cost not reported, counted as $0.25");
  });

  it("shows when, how long, through what, the tokens and the cost on hover, and why the first answer costs", async () => {
    const user = userEvent.setup();
    const { container } = render(<MessageInfo message={answer} first />);
    const about = screen.getByRole("button", { name: "About this answer" });
    expect(screen.getByRole("tooltip", { hidden: true })).not.toBeVisible();
    expect(about).toHaveAccessibleDescription(/Generated/);
    await user.hover(about);
    const panel = screen.getByRole("tooltip");
    expect(panel).toHaveTextContent("3.2 s");
    expect(panel).toHaveTextContent("claude-code");
    expect(panel).not.toHaveTextContent("claude-opus-5-5"); // no model names in the chat (ADR 0158)
    expect(panel).not.toHaveTextContent("effort");
    expect(panel).toHaveTextContent("2 in · 120 out · 15,000 cache read · 800 cache write");
    expect(panel).toHaveTextContent("uses your plan · ≈ $0.1337 at API prices");
    expect(panel).toHaveTextContent("system prompt and tool list with every request");
    await expectAccessible(container);
  });

  it("explains the cost only on a chat's first answer", async () => {
    const user = userEvent.setup();
    render(<MessageInfo message={answer} first={false} />);
    await user.hover(screen.getByRole("button", { name: "About this answer" }));
    expect(screen.getByRole("tooltip")).not.toHaveTextContent("system prompt");
  });
});
