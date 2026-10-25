import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { SchemaAccessList, SchemaAccessOption } from "@/api/client";
import { shell } from "@/test/fixtures";
import { expectAccessible, mockApi, renderAt } from "@/test/render";

function option(name: string, fields: Partial<SchemaAccessOption> = {}): SchemaAccessOption {
  return {
    name,
    title: name === "claude-code" ? "Claude Code" : "Acme AI",
    summary: `${name} for tests`,
    module: "ai.access",
    plugin: null,
    in_use: false,
    status: "signed in",
    detail: "",
    fix: "",
    models: [],
    runner: null,
    hosts: [],
    last_test: null,
    ...fields,
  };
}

function listing(selected: string): SchemaAccessList {
  return {
    selected,
    options: [
      option("claude-code", { in_use: selected === "claude-code" }),
      option("gateway", { in_use: selected === "gateway", status: "not tested" }),
      option("pydantic-ai", {
        title: "Model API",
        status: "not installed",
        fix: "hz system config set ai.access.model",
      }),
    ],
    uses: [],
    note: null,
    next: [],
  };
}

/** The API with an AI access list that follows `hz ai access use` and answers `hz ai access test`. */
function accessApi() {
  let selected = "claude-code";
  return mockApi({
    ...shell,
    get "/api/ai/access/list"() {
      return listing(selected);
    },
    get "/api/ai/access/use"() {
      selected = "gateway";
      return listing(selected);
    },
    "/api/ai/access/test": {
      name: "gateway",
      ok: true,
      detail: "pong",
      fix: "",
      model: null,
      cost_usd: null,
      duration_seconds: 0.1,
    },
  });
}

describe("AI access", () => {
  it("is one choice next to the theme: every option with its status and the one in use", async () => {
    const { calls } = accessApi();
    const user = userEvent.setup();
    const { container } = renderAt("/doctor?settings=hz");
    const group = await screen.findByRole("radiogroup", { name: "AI access" });
    const claude = await within(group).findByRole("radio", { name: /Claude Code/ });
    expect(claude).toHaveAttribute("aria-checked", "true");
    expect(claude).toHaveTextContent("In use");
    expect(within(group).getByRole("radio", { name: /Model API/ })).toBeDisabled();
    expect(within(group).getByText("hz system config set ai.access.model")).toBeInTheDocument();
    await user.click(within(group).getByRole("radio", { name: /Acme AI/ }));
    await waitFor(() =>
      expect(within(group).getByRole("radio", { name: /Acme AI/ })).toHaveAttribute("aria-checked", "true"),
    );
    const use = calls.find((call) => call.url.includes("/ai/access/use"));
    expect(new URL(use?.url ?? "").searchParams.get("name")).toBe("gateway");
    await expectAccessible(container);
  });

  it("tests an option only when asked, and shows what it answered", async () => {
    const { calls } = accessApi();
    const user = userEvent.setup();
    renderAt("/doctor?settings=hz");
    await screen.findByRole("radiogroup", { name: "AI access" });
    expect(calls.some((call) => call.url.includes("/ai/access/test"))).toBe(false);
    await user.click(await screen.findByRole("button", { name: "Test Acme AI; this may cost a little" }));
    expect(await screen.findByText("Answered: pong")).toBeInTheDocument();
    const test = calls.find((call) => call.url.includes("/ai/access/test"));
    expect(new URL(test?.url ?? "").searchParams.get("name")).toBe("gateway");
  });
});
