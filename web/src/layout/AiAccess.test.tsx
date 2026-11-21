// @vitest-environment-options {"url":"http://127.0.0.1:34123"}
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { SchemaAccessList, SchemaAccessOption } from "@/api/client";
import { shell } from "@/test/fixtures";
import { expectAccessible, mockApi, renderAt } from "@/test/render";

// The restart itself is the lifecycle controller's, tested on its own: here it runs the install and tells its phases.
vi.mock("@/lifecycle/controller", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  restartAndApply: async (
    request: { preserve?: (signal: AbortSignal) => Promise<boolean | undefined> },
    options: { onPhase?: (phase: string) => void },
  ) => {
    options.onPhase?.("preserving");
    const restartRequired = await request.preserve?.(new AbortController().signal);
    if (restartRequired === false) return { instance: "before", version: "0.0.0", restartable: true };
    options.onPhase?.("restarting");
    options.onPhase?.("reconnecting");
    options.onPhase?.("applied");
    return { instance: "after", version: "0.0.0", restartable: true };
  },
}));

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
    client: null,
    core_engine: false,
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
        client: "openai",
        fix: "uv tool install --reinstall 'heinzel[openai]'",
      }),
    ],
    uses: [],
    note: null,
    next: [],
  };
}

function chatGptListing(signedIn = false): SchemaAccessList {
  return {
    ...listing("claude-code"),
    options: [
      option("chatgpt", {
        title: "ChatGPT plan",
        status: signedIn ? "signed in" : "not signed in",
        needs: signedIn ? "" : "Sign in to ChatGPT plan",
        setup: signedIn ? null : "sign-in",
        fix: signedIn ? "" : "hz ai access login",
      }),
    ],
  };
}

/** The API with an AI access list that follows `hz ai access use` and answers `hz ai access test`. */
function accessApi(restartRequired = true) {
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
    "/api/ai/access/key": { stored: false },
    "/api/ai/access/set-key": { stored: true },
    "/api/system/plugin/add-extra": { extra: "openai", done: restartRequired, restart_required: restartRequired },
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

  it("lets a plugin's own models be picked, an unavailable one marked but pickable (ADR 0159)", async () => {
    const bridge = option("bridge", {
      title: "Example bridge",
      in_use: true,
      status: "not tested",
      setup: "plugin-model",
      core_engine: true,
      models: ["bridge-fast", "bridge-slow", "bridge-retired"],
      unavailable_models: ["bridge-retired"],
      model: "bridge-fast",
    });
    const { calls } = mockApi({
      ...shell,
      "/api/ai/access/list": { selected: "bridge", options: [option("claude-code"), bridge], uses: [], next: [] },
      "/api/system/config/set": "config.toml",
    });
    const user = userEvent.setup();
    renderAt("/doctor?settings=ai");
    await user.click(await screen.findByRole("button", { name: "Pick a model of Example bridge" }));
    const dialog = await screen.findByRole("dialog", { name: "Pick a model of Example bridge" });
    expect(within(dialog).getByRole("radio", { name: "bridge-fast" })).toHaveAttribute("aria-checked", "true");
    const retired = within(dialog).getByRole("radio", { name: /^bridge-retired/ });
    expect(retired).toHaveTextContent("unavailable");
    expect(retired).toBeEnabled();
    await user.click(within(dialog).getByRole("radio", { name: "bridge-slow" }));
    expect(await within(dialog).findByText("Saved. Test it to see it answer.")).toBeInTheDocument();
    const set = calls.find((call) => call.url.includes("/system/config/set"));
    expect(new URL(set?.url ?? "").searchParams.get("key")).toBe("ai.access.plugin_model");
    expect(new URL(set?.url ?? "").searchParams.get("value")).toBe("bridge-slow");
  });

  it("checks every model of a plugin only after a cost confirmation, with progress, and marks the silent ones", async () => {
    let tested = false;
    let finish = () => {};
    const report = {
      name: "bridge",
      ok: true,
      detail: "1 of 2 models answered; 1 marked unavailable until a test answers; 1 skipped",
      input_tokens: 12,
      output_tokens: 2,
      models: [
        { model: "bridge-fast", outcome: "answered", detail: "OK", duration_seconds: 0.4 },
        { model: "bridge-slow", outcome: "unanswered", detail: "no answer within 15s", duration_seconds: 15 },
        { model: "bridge-retired", outcome: "skipped", detail: "", duration_seconds: 0 },
      ],
      untested: [],
    };
    const bridge = () =>
      option("bridge", {
        title: "Example bridge",
        in_use: true,
        status: "tested",
        setup: "plugin-model",
        core_engine: true,
        models: ["bridge-fast", "bridge-slow", "bridge-retired"],
        unavailable_models: tested ? ["bridge-slow", "bridge-retired"] : ["bridge-retired"],
        model: "bridge-fast",
      });
    const { calls } = mockApi({
      ...shell,
      "/api/ai/access/list": () => ({
        selected: "bridge",
        options: [option("claude-code"), bridge()],
        uses: [],
        next: [],
      }),
      "/api/stream/ai/access/test": () => {
        tested = true;
        return new Response(
          new ReadableStream({
            start(controller) {
              const send = (kind: string, data: object) =>
                controller.enqueue(new TextEncoder().encode(`event: ${kind}\ndata: ${JSON.stringify(data)}\n\n`));
              send("task", { text: "Testing bridge-slow (2/3)" });
              finish = () => {
                send("result", { data: report });
                controller.close();
              };
            },
          }),
          { headers: { "Content-Type": "text/event-stream" } },
        );
      },
    });
    const user = userEvent.setup();
    const { container } = renderAt("/doctor?settings=ai");
    await user.click(await screen.findByRole("button", { name: "Pick a model of Example bridge" }));
    const dialog = await screen.findByRole("dialog", { name: "Pick a model of Example bridge" });
    expect(within(dialog).getByRole("radio", { name: "bridge-slow" })).not.toHaveTextContent("unavailable");
    expect(calls.some((call) => call.url.includes("/ai/access/test"))).toBe(false);
    await user.click(within(dialog).getByRole("button", { name: "Check every model" }));
    expect(within(dialog).getByText(/Heinzel asks each of 3 models one tiny question/)).toBeInTheDocument();
    expect(calls.some((call) => call.url.includes("/ai/access/test"))).toBe(false);
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(within(dialog).queryByText(/Heinzel asks each of 3 models/)).not.toBeInTheDocument();
    expect(calls.some((call) => call.url.includes("/ai/access/test"))).toBe(false);
    await user.click(within(dialog).getByRole("button", { name: "Check every model" }));
    await user.click(within(dialog).getByRole("button", { name: "Check them" }));
    expect(await within(dialog).findByText("Testing bridge-slow (2/3)")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Checking each model…" })).toBeDisabled();
    finish();
    expect(
      await within(dialog).findByText(
        "1 of 2 models answered; 1 marked unavailable until a test answers; 1 skipped; 14 tokens",
      ),
    ).toBeInTheDocument();
    const test = calls.find((call) => call.url.includes("/ai/access/test"));
    expect(new URL(test?.url ?? "").searchParams.get("all")).toBe("true");
    expect(new URL(test?.url ?? "").searchParams.get("name")).toBe("bridge");
    expect(dialog.querySelector('[data-model-test="bridge-slow"]')).toHaveTextContent("didn't answer");
    const picker = () => screen.getByRole("dialog", { name: "Pick a model of Example bridge" });
    await waitFor(() =>
      expect(within(picker()).getByRole("radio", { name: /^bridge-slow/ })).toHaveTextContent("unavailable"),
    );
    expect(within(picker()).getByRole("radio", { name: /^bridge-slow/ })).toBeEnabled();
    await expectAccessible(container);
  });

  it("saves the Model API setup and waits for a restart before testing", async () => {
    const { calls } = accessApi();
    const user = userEvent.setup();
    renderAt("/doctor?settings=ai");
    await user.click(await screen.findByRole("button", { name: "Set up Model API" }));
    const dialog = await screen.findByRole("dialog", { name: "Set up Model API" });
    await user.type(within(dialog).getByLabelText("Model"), "anthropic:claude-sonnet-4-5");
    await user.click(within(dialog).getByRole("button", { name: "Save" }));
    expect(
      await within(dialog).findByText("Saved. Restart Heinzel before testing these settings."),
    ).toBeInTheDocument();
    const set = calls.find((call) => call.url.includes("/system/config/set"));
    expect(new URL(set?.url ?? "").searchParams.get("key")).toBe("ai.access.model");
    expect(new URL(set?.url ?? "").searchParams.get("value")).toBe("anthropic:claude-sonnet-4-5");
    expect(within(dialog).getByRole("button", { name: "Test" })).toBeDisabled();
    expect(calls.some((call) => call.url.includes("/ai/access/test"))).toBe(false);
  });

  it("keeps a typed API key only in the request body, then clears it from the page", async () => {
    const { calls } = accessApi();
    const user = userEvent.setup();
    renderAt("/doctor?settings=ai");
    await user.click(await screen.findByRole("button", { name: "Set up Model API" }));
    const dialog = await screen.findByRole("dialog", { name: "Set up Model API" });
    const field = within(dialog).getByLabelText("API key");
    expect(field).toHaveAttribute("type", "password");
    expect(field).toHaveValue("");
    await user.type(field, "sk-fixture-typed");
    await user.click(within(dialog).getByRole("button", { name: "Save" }));
    expect(await within(dialog).findByText("Saved. Test it to see it answer.")).toBeInTheDocument();
    const set = calls.find((call) => call.url.includes("/ai/access/set-key"));
    expect(set?.url).not.toContain("sk-fixture-typed");
    expect(await set?.clone().json()).toEqual({ value: "sk-fixture-typed" });
    expect(field).toHaveValue("");
    expect(calls.filter((call) => call.url.includes("sk-fixture"))).toEqual([]);
    await user.click(within(dialog).getByRole("button", { name: "Test" }));
    expect(await within(dialog).findByText("Answered: pong")).toBeInTheDocument();
  });

  it("does not report an empty setup as saved, or test edits that have not been saved", async () => {
    const { calls } = accessApi();
    const user = userEvent.setup();
    renderAt("/doctor?settings=ai");
    await user.click(await screen.findByRole("button", { name: "Set up Model API" }));
    const dialog = await screen.findByRole("dialog", { name: "Set up Model API" });
    expect(within(dialog).getByRole("button", { name: "Save" })).toBeDisabled();
    expect(within(dialog).getByRole("button", { name: "Test" })).toBeDisabled();
    await user.type(within(dialog).getByLabelText("Model"), "openai:example");
    await user.click(within(dialog).getByRole("button", { name: "Save" }));
    expect(
      await within(dialog).findByText("Saved. Restart Heinzel before testing these settings."),
    ).toBeInTheDocument();
    await user.type(within(dialog).getByLabelText("Model"), "-changed");
    expect(within(dialog).queryByText("Saved. Restart Heinzel before testing these settings.")).not.toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Test" })).toBeDisabled();
    expect(calls.filter((call) => call.url.includes("/system/config/set"))).toHaveLength(1);
  });

  it("shows a failed access-test request instead of silently losing it", async () => {
    mockApi({
      ...shell,
      "/api/ai/access/list": listing("claude-code"),
      "/api/ai/access/test": { status: 503, body: { error: "Provider is unavailable" } },
    });
    const user = userEvent.setup();
    renderAt("/doctor?settings=ai");
    await user.click(await screen.findByRole("button", { name: "Test Claude Code; this may cost a little" }));
    const dialog = screen.getByRole("dialog", { name: "Settings" });
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Provider is unavailable");
  });

  it("keeps the saved plugin model selected when saving another model fails", async () => {
    mockApi({
      ...shell,
      "/api/ai/access/list": {
        ...listing("bridge"),
        options: [
          option("bridge", {
            title: "Bridge",
            setup: "plugin-model",
            model: "first",
            models: ["first", "second"],
          }),
        ],
      },
      "/api/system/config/set": { status: 400, body: { error: "Model could not be saved" } },
    });
    const user = userEvent.setup();
    renderAt("/doctor?settings=ai");
    await user.click(await screen.findByRole("button", { name: "Pick a model of Bridge" }));
    await user.click(await screen.findByRole("radio", { name: "second" }));
    expect(await screen.findByText("Model could not be saved")).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "first" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("radio", { name: "second" })).toHaveAttribute("aria-checked", "false");
  });

  it("adds a missing client of hz itself with a confirm, then restarts, with no command to type", async () => {
    const { calls } = accessApi();
    const user = userEvent.setup();
    renderAt("/doctor?settings=ai");
    await user.click(await screen.findByRole("button", { name: "Set up ChatGPT plan" }));
    const dialog = await screen.findByRole("dialog", { name: "Set up ChatGPT plan" });
    expect(dialog).toHaveAttribute("data-operation", "/system/plugin/add-extra");
    expect(dialog).toHaveTextContent("hz installs it, then restarts to load it.");
    expect(dialog).not.toHaveTextContent(/uv tool|hz system/);
    expect(calls.some((call) => call.url.includes("/add-extra"))).toBe(false);
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(calls.some((call) => call.url.includes("/add-extra"))).toBe(false);
    await user.click(await screen.findByRole("button", { name: "Set up ChatGPT plan" }));
    const confirmation = await screen.findByRole("dialog", { name: "Set up ChatGPT plan" });
    await user.click(within(confirmation).getByRole("button", { name: "Add and restart" }));
    expect(await screen.findByRole("dialog", { name: "Applied" })).toBe(confirmation);
    const add = calls.find((call) => call.url.includes("/system/plugin/add-extra"));
    expect(new URL(add?.url ?? "").searchParams.get("extra")).toBe("openai");
  });

  it("finishes when a concurrent installation already added the client", async () => {
    accessApi(false);
    const user = userEvent.setup();
    renderAt("/doctor?settings=ai");
    await user.click(await screen.findByRole("button", { name: "Set up ChatGPT plan" }));
    const dialog = await screen.findByRole("dialog", { name: "Set up ChatGPT plan" });
    await user.click(within(dialog).getByRole("button", { name: "Add and restart" }));
    expect(await screen.findByRole("dialog", { name: "Applied" })).toBe(dialog);
    expect(within(dialog).getByRole("button", { name: "Done" })).toBeEnabled();
  });

  it("requires receipt recovery and fresh consent before another install can run", async () => {
    const { calls } = accessApi();
    const user = userEvent.setup();
    sessionStorage.setItem("hz.ai-client-install-pending.v1", "pending");
    renderAt("/doctor?settings=ai");
    await user.click(await screen.findByRole("button", { name: "Set up ChatGPT plan" }));
    const dialog = await screen.findByRole("dialog", { name: "Adding the client for ChatGPT plan" });
    expect(dialog).toHaveTextContent("The server cannot confirm how this installation ended.");
    expect(within(dialog).queryByRole("button", { name: "Try again" })).toBeNull();
    expect(within(dialog).queryByRole("button", { name: "Add and restart" })).toBeNull();
    await user.click(within(dialog).getByRole("button", { name: "Resolve after checking" }));
    await user.click(within(dialog).getByRole("button", { name: "I checked; clear this tab's receipt" }));
    expect(await screen.findByRole("dialog", { name: "Set up ChatGPT plan" })).toBe(dialog);
    expect(calls.filter((call) => call.url.includes("/add-extra"))).toHaveLength(0);
    await user.click(within(dialog).getByRole("button", { name: "Add and restart" }));
    expect(await screen.findByRole("dialog", { name: "Applied" })).toBe(dialog);
    expect(calls.filter((call) => call.url.includes("/add-extra"))).toHaveLength(1);
  });

  it("leaves a sign-in to the person's browser: the command only in Details, and Check again", async () => {
    const { calls } = mockApi({
      ...shell,
      "/api/ai/access/list": chatGptListing(),
    });
    const user = userEvent.setup();
    renderAt("/doctor?settings=ai");
    await user.click(await screen.findByRole("button", { name: "Set up ChatGPT plan" }));
    const dialog = await screen.findByRole("dialog", { name: "Set up ChatGPT plan" });
    const outside = dialog.cloneNode(true) as HTMLElement;
    for (const details of outside.querySelectorAll("details")) details.remove();
    expect(outside.textContent).not.toContain("hz ai access login");
    expect(dialog.querySelector("details")).toHaveTextContent("hz ai access login");
    const before = calls.filter((call) => call.url.includes("/ai/access/list")).length;
    await user.click(within(dialog).getByRole("button", { name: "Check again" }));
    await waitFor(() =>
      expect(calls.filter((call) => call.url.includes("/ai/access/list")).length).toBeGreaterThan(before),
    );
  });

  it("starts the local sign-in only on a click and uses the server's result", async () => {
    let signedIn = false;
    const { calls } = mockApi({
      ...shell,
      "/api/ai/access/list": () => chatGptListing(signedIn),
      "/api/ai/access/chatgpt-sign-in": () => {
        signedIn = true;
        return { outcome: "connected" };
      },
    });
    const user = userEvent.setup();
    renderAt("/doctor?settings=ai");
    await user.click(await screen.findByRole("button", { name: "Set up ChatGPT plan" }));
    const dialog = await screen.findByRole("dialog", { name: "Set up ChatGPT plan" });
    expect(calls.some((call) => call.url.endsWith("chatgpt-sign-in"))).toBe(false);
    await user.click(within(dialog).getByRole("button", { name: "Continue with ChatGPT" }));
    expect(await within(dialog).findByText("Your ChatGPT plan is connected.")).toBeInTheDocument();
    const request = calls.find((call) => call.url.endsWith("chatgpt-sign-in"));
    expect(request?.method).toBe("POST");
    expect(request?.body).toBeNull();
    expect(request?.headers.get("Authorization")).toBe("Bearer test-token");
    await waitFor(() => expect(within(dialog).getByText("Signed in", { exact: true })).toBeInTheDocument());
  });

  it("cancels the pending local sign-in when the dialog closes", async () => {
    const aborted = vi.fn();
    mockApi({
      ...shell,
      "/api/ai/access/list": chatGptListing(),
      "/api/ai/access/chatgpt-sign-in": (request: Request) =>
        new Promise((_, reject) => {
          request.signal.addEventListener("abort", () => {
            aborted();
            reject(new DOMException("Aborted", "AbortError"));
          });
        }),
    });
    const user = userEvent.setup();
    renderAt("/doctor?settings=ai");
    await user.click(await screen.findByRole("button", { name: "Set up ChatGPT plan" }));
    const dialog = await screen.findByRole("dialog", { name: "Set up ChatGPT plan" });
    await user.click(within(dialog).getByRole("button", { name: "Continue with ChatGPT" }));
    expect(within(dialog).getByText("Waiting for you to finish in your browser…")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Close settings" }));
    expect(aborted).toHaveBeenCalledOnce();
  });
});
