import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { Providers, queryClient } from "@/App";
import type { ModelChoice } from "@/api/chat";
import { msg } from "@/test/chat";
import { expectAccessible, mockApi } from "@/test/render";
import { costLabel, MessageInfo } from "./MessageInfo";
import { ModelPicker } from "./ModelPicker";

const models = {
  access: "claude-code",
  title: "Claude Code",
  billing: "plan",
  models: [
    { name: "opus", title: "Opus" },
    { name: "sonnet", title: "Sonnet" },
  ],
  efforts: [
    { name: "low", title: "Low", recommended: false, warning: null },
    { name: "medium", title: "Medium", recommended: true, warning: null },
    { name: "max", title: "Max", recommended: false, warning: "≈3.5x or more usage" },
  ],
  note: null,
};

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
    expect(panel).toHaveTextContent("claude-code · claude-opus-5-5 · effort medium");
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

function Picker() {
  const [choice, setChoice] = useState<ModelChoice>({});
  return (
    <>
      <ModelPicker value={choice} onChange={setChoice} />
      <output aria-label="choice">{JSON.stringify(choice)}</output>
    </>
  );
}

describe("The model and effort picker", () => {
  it("lists the AI access's models with a check mark, the efforts with their warning, and more models", async () => {
    mockApi({ "/api/ai/models": models });
    const user = userEvent.setup();
    const { container } = render(
      <Providers client={queryClient()}>
        <Picker />
      </Providers>,
    );
    const trigger = await screen.findByRole("button", { name: "Model and effort: Default" });
    await user.click(trigger);
    const group = screen.getByRole("group", { name: "Models of Claude Code" });
    const fallback = within(group).getByRole("button", { name: "Default" });
    expect(fallback).toHaveAttribute("aria-pressed", "true");
    expect(fallback).toHaveFocus(); // the popover takes focus
    await expectAccessible(container);
    await user.click(within(group).getByRole("button", { name: "Sonnet" }));
    expect(screen.getByLabelText("choice")).toHaveTextContent('{"model":"sonnet"}');
    const again = screen.getByRole("button", { name: "Model and effort: Sonnet" });
    expect(again).toHaveFocus(); // back on the trigger
    await user.click(again);
    expect(screen.getByRole("button", { name: "Sonnet" })).toHaveAttribute("aria-pressed", "true");
    const efforts = screen.getByRole("group", { name: "Effort" });
    expect(within(efforts).getByRole("button", { name: /Medium \(Recommended\)/ })).toBeInTheDocument();
    const max = within(efforts).getByRole("button", { name: /Max/ });
    expect(max).toHaveTextContent("≈3.5x or more usage");
    await user.click(max);
    expect(screen.getByLabelText("choice")).toHaveTextContent('{"model":"sonnet","effort":"max"}');
    await user.click(screen.getByRole("button", { name: "Model and effort: Sonnet · Max" }));
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("group", { name: "Effort" })).toBeNull();
    expect(screen.getByRole("button", { name: "Model and effort: Sonnet · Max" })).toHaveFocus();
    await user.click(screen.getByRole("button", { name: "Model and effort: Sonnet · Max" }));
    await user.click(within(screen.getByRole("group", { name: "Effort" })).getByRole("button", { name: "Default" }));
    expect(screen.getByLabelText("choice")).toHaveTextContent('"effort":""');
    await user.click(screen.getByRole("button", { name: "Model and effort: Sonnet" }));
    await user.click(screen.getByRole("button", { name: "More models…" }));
    await user.type(screen.getByLabelText("Model name"), "claude-haiku-4-5{Enter}");
    expect(screen.getByLabelText("choice")).toHaveTextContent('"model":"claude-haiku-4-5"');
  });

  it("is absent when the AI access names no models and takes no efforts", async () => {
    mockApi({ "/api/ai/models": { ...models, models: [], efforts: [] } });
    render(
      <Providers client={queryClient()}>
        <Picker />
      </Providers>,
    );
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(screen.queryByRole("button", { name: /Model and effort/ })).toBeNull();
  });
});
