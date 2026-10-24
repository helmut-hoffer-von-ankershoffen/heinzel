import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { doctor, noTeam, shell } from "@/test/fixtures";
import { mockApi, renderAt } from "@/test/render";
import { transcript } from "./ChatPanel";

const answer = {
  runner: "claude-code",
  model: "sonnet",
  status: "completed",
  draft: "Run `hz system doctor`.",
  tool_calls: {},
  turns: 1,
  usage: { cost_usd: 0.0123 },
  duration_seconds: 2.5,
  detail: "",
  warnings: [],
};

describe("ChatPanel", () => {
  it("says it may cost money and names the cap before anything is sent", async () => {
    mockApi({ ...shell, "/api/system/doctor": doctor, "/api/team/show": noTeam, "/api/system/daemon/jobs": [] });
    renderAt("/");
    expect(await screen.findByText(/may cost money: at most \$0\.25 per answer/)).toBeInTheDocument();
  });

  it("sends the conversation to `hz system runner ask` and shows the answer with its cost", async () => {
    const { calls } = mockApi({
      ...shell,
      "/api/system/doctor": doctor,
      "/api/team/show": noTeam,
      "/api/system/daemon/jobs": [],
      "/api/system/runner/ask": answer,
    });
    const user = userEvent.setup();
    renderAt("/");
    await user.type(screen.getByLabelText("Your message"), "How healthy is hz?{Enter}");
    expect(await screen.findByText("Run `hz system doctor`.")).toBeInTheDocument();
    expect(screen.getByText("claude-code · $0.0123")).toBeInTheDocument();
    const ask = calls.find((request) => request.url.includes("/api/system/runner/ask"));
    expect(ask?.method).toBe("POST");
    expect(new URL(ask?.url ?? "").searchParams.get("prompt")).toBe("User: How healthy is hz?");
  });

  it("says when a run was stopped for its budget", async () => {
    mockApi({
      ...shell,
      "/api/system/doctor": doctor,
      "/api/team/show": noTeam,
      "/api/system/daemon/jobs": [],
      "/api/system/runner/ask": { ...answer, status: "budget", draft: "", detail: "over $0.25" },
    });
    const user = userEvent.setup();
    renderAt("/");
    await user.type(screen.getByLabelText("Your message"), "Write a novel{Enter}");
    expect(await screen.findByText("Stopped: over $0.25")).toBeInTheDocument();
  });
});

describe("transcript", () => {
  it("keeps the most recent turns that fit, oldest first, ending with the new message", () => {
    const turns = [
      { role: "user" as const, text: "a".repeat(50) },
      { role: "agent" as const, text: "first answer" },
      { role: "user" as const, text: "second" },
    ];
    expect(transcript(turns, "third", 60)).toBe("Assistant: first answer\n\nUser: second\n\nUser: third");
  });
});
