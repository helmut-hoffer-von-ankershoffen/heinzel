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
    needs: "",
    setup: null,
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
        status: "needs setup",
        needs: "Add a model and API key",
        setup: "model",
        detail: "no model is configured",
        fix: "hz system config set ai.access.model",
      }),
      option("chatgpt", {
        title: "ChatGPT plan",
        status: "needs setup",
        needs: "Add the OpenAI client",
        setup: "client",
        fix: "uv tool install --reinstall 'heinzel[openai]'",
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
    "/api/system/config/set": "config.toml",
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
    const { container } = renderAt("/doctor?settings=ai");
    const group = await screen.findByRole("radiogroup", { name: "AI access" });
    const claude = await within(group).findByRole("radio", { name: /Claude Code/ });
    expect(claude).toHaveAttribute("aria-checked", "true");
    expect(claude).toHaveTextContent("In use");
    expect(within(group).getByRole("radio", { name: /Model API/ })).toBeDisabled();
    expect(within(group).getByRole("radio", { name: /Model API/ })).toHaveTextContent(
      "Needs setup: Add a model and API key",
    );
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
    renderAt("/doctor?settings=ai");
    await screen.findByRole("radiogroup", { name: "AI access" });
    expect(calls.some((call) => call.url.includes("/ai/access/test"))).toBe(false);
    await user.click(await screen.findByRole("button", { name: "Test Acme AI; this may cost a little" }));
    expect(await screen.findByText("Answered: pong")).toBeInTheDocument();
    const test = calls.find((call) => call.url.includes("/ai/access/test"));
    expect(new URL(test?.url ?? "").searchParams.get("name")).toBe("gateway");
  });

  it("never shows a command outside a card's Details, nor asks for a restart", async () => {
    accessApi();
    const { container } = renderAt("/doctor?settings=ai");
    await screen.findByRole("radio", { name: /Model API/ });
    const commands = /\bhz (system|ai|ui) |uv tool|restart/;
    for (const card of container.querySelectorAll("[data-option]")) {
      const outside = card.cloneNode(true) as HTMLElement;
      for (const details of outside.querySelectorAll("details")) details.remove();
      expect(outside.textContent ?? "").not.toMatch(commands);
    }
    const model = container.querySelector('[data-option="pydantic-ai"] details');
    expect(model?.textContent).toContain("hz system config set ai.access.model");
    expect(container.textContent).not.toMatch(/restart `?hz ui serve/);
  });

  it("sets the Model API up in a dialog, then tests it there with the result inline", async () => {
    const { calls } = accessApi();
    const user = userEvent.setup();
    renderAt("/doctor?settings=ai");
    await user.click(await screen.findByRole("button", { name: "Set up Model API" }));
    const dialog = await screen.findByRole("dialog", { name: "Set up Model API" });
    await user.type(within(dialog).getByLabelText("Model"), "anthropic:claude-sonnet-4-5");
    await user.click(within(dialog).getByRole("button", { name: "Save" }));
    expect(await within(dialog).findByText("Saved. Test it to see it answer.")).toBeInTheDocument();
    const set = calls.find((call) => call.url.includes("/system/config/set"));
    expect(new URL(set?.url ?? "").searchParams.get("key")).toBe("ai.access.model");
    expect(new URL(set?.url ?? "").searchParams.get("value")).toBe("anthropic:claude-sonnet-4-5");
    await user.click(within(dialog).getByRole("button", { name: "Test" }));
    expect(await within(dialog).findByText("Answered: pong")).toBeInTheDocument();
  });
});
