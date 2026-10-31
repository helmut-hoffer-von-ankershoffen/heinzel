import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Providers, queryClient } from "@/App";
import { expectAccessible } from "@/test/render";
import { ModuleHeader, PluginsSection, sourceLabel } from "./PluginsSection";

const PINNED = `heinzel-crm @ git+https://github.com/acme-example/heinzel-crm@${"b".repeat(40)}`;

const installed = {
  distribution: "heinzel-clock",
  version: "0.3.0",
  modules: ["clock"],
  name: "clock",
  summary: "What time it is for everyone on the team.",
  source_kind: "git" as const,
  source_url: "https://github.com/helmut-hoffer-von-ankershoffen/heinzel/tree/v0.3.0/plugins/heinzel-clock",
  revision: "abc",
  homepage: null,
  first_party: true,
  enabled: true,
  error: null,
  layers: [],
  capabilities: [],
  playbooks: [],
};

const candidate = {
  spec: "git+https://github.com/acme-example/heinzel-crm@v1.2.0",
  requirement: PINNED,
  distribution: "heinzel-crm",
  version: "1.2.0",
  summary: "CRM notes.",
  source: "git",
  url: "https://github.com/acme-example/heinzel-crm",
  publisher: "acme-example (Ada Example)",
  first_party: false,
  private: true,
  revision: "b".repeat(40),
  installed_version: null,
  warnings: ["It is named crm-tools, not heinzel-<name> as plugins are (ADR 0025)."],
};
let receiptCount = 0;
let confirmedStreamDelay = 0;

describe("plugin source labels", () => {
  it.each([
    ["https://github.com/helmut-hoffer-von-ankershoffen/heinzel/tree/main/plugins/heinzel-clock", null, "First-Party"],
    ["https://github.com/HELMUT-HOFFER-VON-ANKERSHOFFEN/HEINZEL.git", null, "First-Party"],
    [
      "https://github.com/helmut-hoffer-von-ankershoffen/heinzel-plugins/tree/main/plugins/heinzel-clock",
      null,
      "First-Party",
    ],
    ["https://github.com/helmut-hoffer-von-ankershoffen/heinzel-plugins-fork", true, "Private"],
    ["https://github.com/helmut-hoffer-von-ankershoffen/heinzel-contrib", null, "Community"],
    ["https://github.com/helmut-hoffer-von-ankershoffen/heinzel-fork", true, "Private"],
    ["https://github.com/helmut-hoffer-von-ankershoffen/heinzel-acme", false, "Custom"],
    ["https://github.com/another-owner/heinzel", false, "Custom"],
    ["https://github.com.evil.example/helmut-hoffer-von-ankershoffen/heinzel", false, "Custom"],
    ["https://github.com:8443/helmut-hoffer-von-ankershoffen/heinzel", null, "source not reported yet"],
    [null, null, "source not reported yet"],
  ])("uses repository identity despite a stale legacy first-party flag: %s", (url, visibility, expected) => {
    expect(sourceLabel({ ...installed, source_url: url }, visibility)).toBe(expected);
  });

  it("labels the actual installed repository instead of a catalog match", () => {
    const plugin = {};
    expect(sourceLabel(plugin, true, "https://github.com/acme-example/private-fork")).toBe("Private");
    expect(sourceLabel(plugin, false, "https://github.com/acme-example/public-plugin")).toBe("Custom");
    expect(
      sourceLabel(
        plugin,
        true,
        "https://github.com/helmut-hoffer-von-ankershoffen/heinzel-contrib/tree/main/plugins/heinzel-weather",
      ),
    ).toBe("Community");
  });

  it("does not infer community provenance from an unknown or missing source", () => {
    const plugin = {};
    expect(sourceLabel(plugin, null, null)).toBe("source not reported yet");
    expect(sourceLabel(plugin, null, "https://github.com/acme-example/heinzel-contrib")).toBe(
      "source not reported yet",
    );
  });

  it("uses the repository in a Settings header even when legacy metadata calls the plugin first-party", () => {
    render(
      <ModuleHeader
        title="Conditions"
        plugin={{
          ...installed,
          source_url:
            "https://github.com/helmut-hoffer-von-ankershoffen/heinzel-contrib/tree/main/plugins/heinzel-clock",
        }}
        version="0.3.0"
        onOpen={() => {}}
      />,
    );
    expect(screen.getByRole("button", { name: "Community" })).toBeInTheDocument();
    expect(screen.queryByText("First-Party")).not.toBeInTheDocument();
  });
});

function change(action: string, done: boolean, extra: Record<string, unknown> = {}) {
  return {
    action,
    done,
    distribution: "heinzel-crm",
    candidate: action === "add" ? candidate : null,
    plugins: [installed],
    restart_required: done,
    next: [],
    ...extra,
  };
}

type Answer = (url: URL, request: Request) => { status?: number; body: unknown } | undefined;
let lifecycleReads = 0;

/** Answer the API by path and query, recording every request. */
function serve(answer: Answer) {
  const calls: Request[] = [];
  const receipts = new Map<string, typeof candidate>();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      calls.push(request);
      const url = new URL(request.url);
      let found = answer(url, request);
      if (
        (url.pathname === "/api/system/plugin/add" || url.pathname === "/api/stream/system/plugin/add") &&
        !url.searchParams.has("confirm")
      ) {
        receiptCount += 1;
      }
      const receiptId = String(receiptCount).padStart(43, "0");
      if (url.pathname === "/api/system/plugin/operation" && !found) {
        const id = url.searchParams.get("operation_id") ?? receiptId;
        found = {
          body: {
            operation_id: id,
            status: "previewed",
            candidate: receipts.get(id) ?? candidate,
            result: null,
          },
        };
      }
      if (
        found &&
        (url.pathname === "/api/system/plugin/add" || url.pathname === "/api/stream/system/plugin/add") &&
        typeof found.body === "object" &&
        found.body !== null
      ) {
        const body = found.body as Record<string, unknown>;
        const id =
          url.searchParams.get("confirm") === "true" ? (url.searchParams.get("operation_id") ?? receiptId) : receiptId;
        const proposed = body.candidate;
        if (typeof proposed === "object" && proposed !== null && !url.searchParams.has("confirm"))
          receipts.set(id, proposed as typeof candidate);
        found = {
          ...found,
          body: {
            ...body,
            ...(!url.searchParams.has("confirm") ? { operation_id: id } : {}),
            ...(url.searchParams.get("confirm") === "true"
              ? { operation_id: url.searchParams.get("operation_id") }
              : {}),
          },
        };
      }
      if (!found) return Response.json({ error: `no answer for ${url.pathname}` }, { status: 404 });
      if (url.pathname.startsWith("/api/stream/")) {
        const text = url.pathname.endsWith("/remove") ? "Removing heinzel-clock" : "Adding plugin heinzel-crm";
        const task = `event: task\ndata: ${JSON.stringify({ text })}\n\n`;
        const result = `event: result\ndata: ${JSON.stringify({ data: found.body })}\n\n`;
        if (
          confirmedStreamDelay > 0 &&
          (url.searchParams.get("confirm") === "true" || url.pathname === "/api/stream/system/plugin/remove")
        ) {
          let timer: ReturnType<typeof setTimeout> | undefined;
          return new Response(
            new ReadableStream<Uint8Array>({
              start(controller) {
                const encoder = new TextEncoder();
                controller.enqueue(encoder.encode(task));
                timer = setTimeout(() => {
                  controller.enqueue(encoder.encode(result));
                  controller.close();
                }, confirmedStreamDelay);
              },
              cancel() {
                clearTimeout(timer);
              },
            }),
            { status: found.status ?? 200, headers: { "Content-Type": "text/event-stream" } },
          );
        }
        const events = [task, result].join("");
        return new Response(events, { status: found.status ?? 200, headers: { "Content-Type": "text/event-stream" } });
      }
      return Response.json(found.body, { status: found.status ?? 200 });
    }),
  );
  return calls;
}

function base(url: URL): { status?: number; body: unknown } | undefined {
  switch (url.pathname) {
    case "/api/lifecycle":
      lifecycleReads += 1;
      return { body: { instance: lifecycleReads === 1 ? "old" : "new", version: "0.3.0", restartable: true } };
    case "/api/lifecycle/verify":
      return { body: { instance: "new", version: "0.3.0", matches: true, mismatched: [] } };
    case "/api/system/plugin/list":
      return { body: [installed] };
    case "/api/system/plugin/search":
      return { body: { plugins: [], warnings: [] } };
    case "/api/restart":
      return { status: 202, body: { restarting: true, instance: "old", message: "Restarting" } };
    case "/api/system/info":
      return { body: { version: "0.3.0" } };
    default:
      return undefined;
  }
}

function show(props: Parameters<typeof PluginsSection>[0] = {}) {
  sessionStorage.setItem("hz.token", "test-token");
  return render(
    <Providers client={queryClient()}>
      <PluginsSection {...props} />
    </Providers>,
  );
}

beforeEach(() => {
  sessionStorage.clear();
  receiptCount = 0;
  confirmedStreamDelay = 0;
  lifecycleReads = 0;
});
afterEach(() => vi.unstubAllGlobals());

describe("PluginsSection", () => {
  it("does not call an uncompleted backend reply an installed plugin or restart the server", async () => {
    const calls = serve((url) =>
      url.pathname === "/api/system/plugin/add" || url.pathname === "/api/stream/system/plugin/add"
        ? { body: change("add", false) }
        : base(url),
    );
    const user = userEvent.setup();
    const reload = vi.fn();
    show({ reload, restartInterval: 1 });
    await user.click(screen.getByRole("button", { name: "Add" }));
    await user.type(screen.getByLabelText(/A plugin's name/), candidate.spec);
    await user.click(screen.getByRole("button", { name: "Install" }));
    await user.click(
      within(await screen.findByRole("group", { name: /Install this plugin/ })).getByRole("button", {
        name: "Restart and apply",
      }),
    );
    await user.click(await screen.findByRole("button", { name: "Restart and apply Plugins" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("cannot confirm how this installation ended");
    expect(screen.queryByRole("button", { name: "Remove heinzel-crm" })).not.toBeInTheDocument();
    expect(calls.filter((request) => new URL(request.url).pathname === "/api/restart")).toHaveLength(0);
    expect(await screen.findByRole("button", { name: "Resolve after checking" })).toBeInTheDocument();
    expect(calls.filter((request) => new URL(request.url).searchParams.get("confirm") === "true")).toHaveLength(1);
  });

  it("reconciles a completed server receipt after reload without replaying the install", async () => {
    const operationId = "z".repeat(43);
    const result = change("add", true, { operation_id: operationId });
    const calls = serve((url) =>
      url.pathname === "/api/system/plugin/operation"
        ? { body: { operation_id: operationId, status: "completed", candidate, result } }
        : base(url),
    );
    sessionStorage.setItem("hz.plugin-add-operation.v1", operationId);
    const reload = vi.fn();
    const user = userEvent.setup();
    show({ reload, restartInterval: 1 });

    await user.click(await screen.findByRole("button", { name: "Restart and apply" }));
    await user.click(await screen.findByRole("button", { name: "Restart and apply Plugins" }));
    await waitFor(() => expect(reload).toHaveBeenCalledOnce());

    expect(calls.filter((request) => new URL(request.url).pathname === "/api/stream/system/plugin/add")).toHaveLength(
      0,
    );
    expect(sessionStorage.getItem("hz.plugin-add-operation.v1")).toBeNull();
    expect(
      calls.filter((request) => new URL(request.url).pathname === "/api/system/plugin/operation").length,
    ).toBeGreaterThan(0);
  });

  it("shows a server-confirmed running receipt after reload without offering another install request", async () => {
    const operationId = "y".repeat(43);
    const calls = serve((url) =>
      url.pathname === "/api/system/plugin/operation"
        ? { body: { operation_id: operationId, status: "running", candidate, result: null } }
        : base(url),
    );
    sessionStorage.setItem("hz.plugin-add-operation.v1", operationId);
    show();

    expect(await screen.findByText(/server is still working on this installation/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Restart and apply" })).not.toBeInTheDocument();
    expect(calls.filter((request) => new URL(request.url).pathname === "/api/stream/system/plugin/add")).toHaveLength(
      0,
    );
  });

  it("moves a completed install to Installed and reconciles a stale Discover snapshot without restarting", async () => {
    let done = false;
    const crm = { ...installed, distribution: "heinzel-crm", name: "crm", version: "1.2.0", summary: "CRM notes." };
    const calls = serve((url) => {
      if (url.pathname === "/api/system/plugin/list") return { body: done ? [installed, crm] : [installed] };
      if (url.pathname === "/api/system/plugin/search")
        return {
          body: {
            plugins: [
              {
                name: "heinzel-crm",
                short_name: "crm",
                description: "CRM notes.",
                source: "github",
                install_spec: candidate.spec,
                installed: false,
                community: true,
              },
            ],
            warnings: [],
          },
        };
      if (url.pathname === "/api/system/plugin/add" || url.pathname === "/api/stream/system/plugin/add") {
        done = url.searchParams.get("confirm") === "true";
        return {
          body: change("add", done, { plugins: done ? [installed, crm] : [installed], restart_required: false }),
        };
      }
      return base(url);
    });
    const reload = vi.fn();
    const user = userEvent.setup();
    show({ reload });
    await user.click(screen.getByRole("tab", { name: "Discover" }));
    await user.click(await screen.findByRole("button", { name: "Install" }));
    await user.click(
      within(await screen.findByRole("group", { name: /Install this plugin/ })).getByRole("button", {
        name: "Restart and apply",
      }),
    );
    await user.click(await screen.findByRole("button", { name: "Restart and apply Plugins" }));
    expect(await screen.findByText("CRM", { exact: true })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Installed" })).toHaveAttribute("aria-selected", "true");
    expect(
      within(screen.getByText("CRM", { exact: true }).closest("li") as HTMLElement).getByText("v1.2.0"),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Discover" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Install" })).not.toBeInTheDocument());
    const inventory = calls.filter((call) => new URL(call.url).pathname === "/api/system/plugin/list");
    expect(inventory.every((call) => new URL(call.url).searchParams.get("fresh") === "true")).toBe(true);
    expect(calls.filter((call) => new URL(call.url).pathname === "/api/restart")).toHaveLength(0);
    expect(reload).not.toHaveBeenCalled();
  });

  it("keeps the installation result visible after a refused restart and never replays the confirmed add", async () => {
    let installedNow = false;
    const crm = { ...installed, distribution: "heinzel-crm", name: "crm", summary: "CRM notes." };
    const calls = serve((url) => {
      if (url.pathname === "/api/system/plugin/list") return { body: installedNow ? [installed, crm] : [installed] };
      if (url.pathname === "/api/restart") return { status: 400, body: { error: "Restart refused fixture" } };
      if (url.pathname === "/api/system/plugin/operation") {
        const operationId = url.searchParams.get("operation_id") ?? "";
        return {
          body: {
            operation_id: operationId,
            status: installedNow ? "completed" : "previewed",
            candidate,
            result: installedNow ? change("add", true, { operation_id: operationId }) : null,
          },
        };
      }
      if (url.pathname === "/api/system/plugin/add" || url.pathname === "/api/stream/system/plugin/add") {
        installedNow = url.searchParams.get("confirm") === "true";
        return { body: change("add", installedNow, { plugins: installedNow ? [installed, crm] : [installed] }) };
      }
      return base(url);
    });
    const user = userEvent.setup();
    const reload = vi.fn();
    const view = show({ reload, restartInterval: 1 });
    await user.click(screen.getByRole("button", { name: "Add" }));
    await user.type(screen.getByLabelText(/A plugin's name/), candidate.spec);
    await user.click(screen.getByRole("button", { name: "Install" }));
    const confirm = async () => {
      await user.click(
        within(screen.getByRole("group", { name: /Install this plugin/ })).getByRole("button", {
          name: "Restart and apply",
        }),
      );
      await user.click(await screen.findByRole("button", { name: "Restart and apply Plugins" }));
    };
    await screen.findByRole("group", { name: /Install this plugin/ });
    await confirm();
    expect(await screen.findByRole("alert")).toHaveTextContent("Restart refused fixture");
    expect(screen.getByText("CRM", { exact: true })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Check again" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Restart and apply" })).not.toBeInTheDocument();
    expect(calls.filter((call) => new URL(call.url).searchParams.get("confirm") === "true")).toHaveLength(1);
    expect(calls.filter((call) => new URL(call.url).pathname === "/api/restart")).toHaveLength(1);

    const savedRestart = JSON.parse(sessionStorage.getItem("hz.plugin-add-restart.v1") ?? "null");
    expect(savedRestart.observation.retired).toContain("old");
    view.unmount();
    show({ reload, restartInterval: 1 });
    expect(await screen.findByRole("alert")).toHaveTextContent("restart may already have been accepted");
    expect(screen.queryByRole("button", { name: "Restart and apply" })).not.toBeInTheDocument();
    await user.click(await screen.findByRole("button", { name: "Check again" }));
    await waitFor(() => expect(reload).toHaveBeenCalledOnce());
    expect(calls.filter((call) => new URL(call.url).pathname === "/api/restart")).toHaveLength(1);
    expect(calls.filter((call) => new URL(call.url).searchParams.get("confirm") === "true")).toHaveLength(1);
  });

  it("shows what a spec would install first, and installs the pinned requirement only on confirm", async () => {
    const calls = serve((url) => {
      if (url.pathname === "/api/system/plugin/add" || url.pathname === "/api/stream/system/plugin/add") {
        return {
          body: change("add", url.searchParams.get("confirm") === "true"),
        };
      }
      return base(url);
    });
    const user = userEvent.setup();
    const reload = vi.fn();
    const { container } = show({ reload, restartInterval: 1 });
    await user.click(screen.getByRole("button", { name: "Add" }));
    await user.type(screen.getByLabelText(/A plugin's name/), candidate.spec);
    await user.click(screen.getByRole("button", { name: "Install" }));

    const preview = await screen.findByRole("group", { name: /Install this plugin/ });
    const adds = () =>
      calls.filter((request) => {
        const path = new URL(request.url).pathname;
        return path === "/api/system/plugin/add" || path === "/api/stream/system/plugin/add";
      });
    expect(adds()).toHaveLength(1);
    expect(new URL(adds()[0]?.url ?? "").searchParams.get("spec")).toBe(candidate.spec);
    expect(new URL(adds()[0]?.url ?? "").searchParams.get("confirm")).toBeNull();
    for (const text of ["heinzel-crm", "1.2.0", "acme-example (Ada Example)", PINNED, candidate.warnings[0] ?? ""]) {
      expect(within(preview).getByText(text)).toBeInTheDocument();
    }
    expect(within(preview).getByText(/private repository/)).toBeInTheDocument();
    expect(
      within(preview).getByRole("link", {
        name: "github.com/acme-example/heinzel-crm",
      }),
    ).toHaveAttribute("href", candidate.url);
    await expectAccessible(container);

    expect(adds()).toHaveLength(1);
    await user.click(within(preview).getByRole("button", { name: "Restart and apply" }));
    await user.click(await screen.findByRole("button", { name: "Restart and apply Plugins" }));
    await waitFor(() => expect(reload).toHaveBeenCalledTimes(1));
    expect(calls.some((request) => new URL(request.url).pathname === "/api/restart")).toBe(true);
    const confirmed = new URL(adds()[1]?.url ?? "");
    expect(confirmed.pathname).toBe("/api/stream/system/plugin/add");
    expect(confirmed.searchParams.get("spec")).toBe(PINNED);
    expect(confirmed.searchParams.get("confirm")).toBe("true");
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  it.each(["add", "remove"])(
    "keeps streamed %s alive beyond the four-second request bound",
    { timeout: 15_000 },
    async (action) => {
      confirmedStreamDelay = 4_250;
      const calls = serve((url) => {
        if (url.pathname === "/api/system/plugin/add" || url.pathname === "/api/stream/system/plugin/add") {
          return { body: change("add", url.searchParams.get("confirm") === "true") };
        }
        if (url.pathname === "/api/stream/system/plugin/remove") {
          return { body: change("remove", true, { distribution: "heinzel-clock", plugins: [] }) };
        }
        return base(url);
      });
      const user = userEvent.setup();
      const reload = vi.fn();
      show({ reload, restartInterval: 1 });
      if (action === "add") {
        await user.click(screen.getByRole("button", { name: "Add" }));
        await user.type(screen.getByLabelText(/A plugin's name/), candidate.spec);
        await user.click(screen.getByRole("button", { name: "Install" }));
      } else {
        await user.click(await screen.findByRole("button", { name: "Remove heinzel-clock" }));
      }
      const preview = await screen.findByRole("group", {
        name: action === "add" ? /Install this plugin/ : /Remove heinzel-clock/,
      });
      await user.click(within(preview).getByRole("button", { name: "Restart and apply" }));
      await user.click(await screen.findByRole("button", { name: "Restart and apply Plugins" }));

      expect(
        await screen.findByText(action === "add" ? "Adding plugin heinzel-crm" : "Removing heinzel-clock"),
      ).toBeInTheDocument();
      await waitFor(() => expect(reload).toHaveBeenCalledOnce(), { timeout: 10_000 });
      const paths = calls.map((request) => new URL(request.url).pathname);
      expect(paths.filter((path) => path === `/api/stream/system/plugin/${action}`)).toHaveLength(1);
      expect(paths.filter((path) => path === "/api/system/plugin/remove")).toHaveLength(0);
      expect(paths.filter((path) => path === "/api/restart")).toHaveLength(1);
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    },
  );

  it("does not repeat an uncertain removal in the same confirmation", async () => {
    const calls = serve((url) => {
      if (url.pathname === "/api/stream/system/plugin/remove") {
        return { body: change("remove", true, { distribution: "heinzel-clock", plugins: [] }) };
      }
      return base(url);
    });
    const originalFetch = vi.mocked(fetch).getMockImplementation();
    if (!originalFetch) throw new Error("missing fetch fixture");
    vi.mocked(fetch).mockImplementation(async (request) => {
      const response = await originalFetch(request);
      if (new URL((request as Request).url).pathname === "/api/stream/system/plugin/remove") {
        throw new Error("response lost");
      }
      return response;
    });
    const user = userEvent.setup();
    show({ reload: vi.fn(), restartInterval: 1 });
    await user.click(await screen.findByRole("button", { name: "Remove heinzel-clock" }));
    const preview = await screen.findByRole("group", { name: /Remove heinzel-clock/ });
    await user.click(within(preview).getByRole("button", { name: "Restart and apply" }));
    await user.click(await screen.findByRole("button", { name: "Restart and apply Plugins" }));
    expect(await screen.findAllByText("response lost")).not.toHaveLength(0);
    await user.click(within(preview).getByRole("button", { name: "Restart and apply" }));
    await user.click(await screen.findByRole("button", { name: "Restart and apply Plugins" }));
    expect(await screen.findByText(/The request result is unknown/)).toBeInTheDocument();
    expect(calls.filter((call) => new URL(call.url).pathname === "/api/stream/system/plugin/remove")).toHaveLength(1);
    expect(calls.filter((call) => new URL(call.url).pathname === "/api/restart")).toHaveLength(0);
    // Reopening the still-present plugin is a new explicit choice after the invalidated list has been read.
    await user.click(within(preview).getByRole("button", { name: "Cancel" }));
    await user.click(await screen.findByRole("button", { name: "Remove heinzel-clock" }));
    const reopened = await screen.findByRole("group", { name: /Remove heinzel-clock/ });
    await user.click(within(reopened).getByRole("button", { name: "Restart and apply" }));
    await user.click(await screen.findByRole("button", { name: "Restart and apply Plugins" }));
    expect(await screen.findAllByText("response lost")).not.toHaveLength(0);
    expect(calls.filter((call) => new URL(call.url).pathname === "/api/stream/system/plugin/remove")).toHaveLength(2);
  });

  it("installs a first-party plugin from Discover by name and a community one by its spec", async () => {
    const entry = (name: string, community: boolean, install_spec: string, source?: string) => ({
      name: `heinzel-${name}`,
      short_name: name,
      description: `The ${name} plugin.`,
      source: source ?? (community ? "github" : "first-party"),
      community,
      install_spec,
      url: `https://example.org/${name}`,
      repository_url:
        source === "contrib"
          ? `https://github.com/helmut-hoffer-von-ankershoffen/heinzel-contrib/tree/main/plugins/heinzel-${name}`
          : !community
            ? `https://github.com/helmut-hoffer-von-ankershoffen/heinzel/tree/main/plugins/heinzel-${name}`
            : `https://github.com/acme-example/heinzel-${name}`,
      private_repository: false,
      stars: null,
      installed: false,
    });
    const git = "git+https://github.com/acme-example/heinzel-crm";
    const calls = serve((url) => {
      if (url.pathname === "/api/system/plugin/search") {
        return {
          body: {
            plugins: [
              entry("zoom", false, "zoom"),
              entry("crm", true, git),
              entry("weather", true, "w", "contrib"),
              {
                ...entry("reference", false, "reference"),
                repository_url: "https://github.com/helmut-hoffer-von-ankershoffen/heinzel-acme",
              },
              { ...entry("private-tools", true, "private"), private_repository: true },
              { ...entry("unknown", true, "unknown"), private_repository: null },
            ],
            warnings: [],
          },
        };
      }
      if (url.pathname === "/api/system/plugin/add" || url.pathname === "/api/stream/system/plugin/add") {
        const first =
          url.searchParams.get("spec") === "zoom" || url.searchParams.get("spec") === "heinzel-zoom @ git+x";
        return {
          body: change("add", url.searchParams.get("confirm") === "true", {
            candidate: {
              ...candidate,
              first_party: first,
              spec: url.searchParams.get("spec"),
              requirement: first ? "heinzel-zoom @ git+x" : PINNED,
            },
          }),
        };
      }
      return base(url);
    });
    const user = userEvent.setup();
    show({ reload: vi.fn(), restartInterval: 1 });
    await user.click(screen.getByRole("tab", { name: "Discover" }));
    const labels = (await screen.findAllByRole("listitem")).map((row) => row.textContent);
    expect(labels.some((text) => text?.includes("Community"))).toBe(true);
    expect(labels.some((text) => text?.includes("Custom"))).toBe(true);
    expect(labels.some((text) => text?.includes("First-Party"))).toBe(true);
    const row = (name: string) => screen.getByText(name, { exact: true }).closest("li") as HTMLElement;
    expect(within(row("Reference")).getByText("Custom")).toBeInTheDocument();
    expect(within(row("Private tools")).getByText("Private")).toBeInTheDocument();
    expect(within(row("Unknown")).getByText("source not reported yet")).toBeInTheDocument();
    const adds = () =>
      calls.filter((request) => {
        const path = new URL(request.url).pathname;
        return path === "/api/system/plugin/add" || path === "/api/stream/system/plugin/add";
      });
    const spec = (index: number) => new URL(adds()[index]?.url ?? "").searchParams.get("spec");

    const installs = await screen.findAllByRole("button", {
      name: "Install",
    });
    await user.click(installs[0] as HTMLElement);
    expect(spec(0)).toBe("zoom");
    await user.click(
      within(await screen.findByRole("group", { name: /Install/ })).getByRole("button", { name: "Restart and apply" }),
    );
    await user.click(await screen.findByRole("button", { name: "Restart and apply Plugins" }));
    await waitFor(() => expect(adds()).toHaveLength(2));
    expect(spec(1)).toBe("zoom");

    await waitFor(() => expect(screen.queryByRole("group", { name: /Install/ })).not.toBeInTheDocument());
    await user.click(screen.getByRole("tab", { name: "Discover" }));

    await user.click((await screen.findAllByRole("button", { name: "Install" }))[1] as HTMLElement);
    expect(spec(2)).toBe(git);
    await user.click(
      within(await screen.findByRole("group", { name: /Install/ })).getByRole("button", { name: "Restart and apply" }),
    );
    await user.click(await screen.findByRole("button", { name: "Restart and apply Plugins" }));
    await waitFor(() => expect(adds()).toHaveLength(4));
    expect(spec(3)).toBe(PINNED);
  });

  it("opens the confirmation under the clicked Discover row, focused, one at a time, with provenance collapsed", async () => {
    const entry = (name: string) => ({
      name: `heinzel-${name}`,
      short_name: name,
      description: `The ${name} plugin.`,
      source: "github",
      community: true,
      install_spec: `heinzel-${name}`,
      url: `https://example.org/${name}`,
      repository_url: `https://github.com/acme-example/heinzel-${name}`,
      private_repository: false,
      stars: null,
      installed: false,
    });
    serve((url) => {
      if (url.pathname === "/api/system/plugin/search") {
        return { body: { plugins: ["aa", "bb", "cc"].map(entry), warnings: [] } };
      }
      if (url.pathname === "/api/system/plugin/add") return { body: change("add", false) };
      return base(url);
    });
    const user = userEvent.setup();
    show({ restartInterval: 1 });
    await user.click(screen.getByRole("tab", { name: "Discover" }));
    const rows = await screen.findAllByRole("listitem");
    expect(rows).toHaveLength(3);
    const row = rows[2] as HTMLElement;
    const install = within(row).getByRole("button", { name: "Install" });
    await user.click(install);
    const card = await screen.findByRole("group", { name: /Install this plugin/ });
    expect(row).toContainElement(card);
    expect(screen.getAllByRole("group", { name: /Install this plugin/ })).toHaveLength(1);
    expect(card.closest("[data-plugin-confirm]")).toHaveFocus();
    expect(within(row).getByRole("button", { name: "Install" })).toHaveAttribute("aria-expanded", "true");
    expect(card.querySelector("details")).not.toHaveAttribute("open");
    expect(screen.getByRole("tab", { name: "Installed" })).toBeDisabled();
    await user.click(within(card).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("group", { name: /Install this plugin/ })).not.toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Installed" })).toBeEnabled();
  });

  it("says why a source is refused and installs nothing", async () => {
    const calls = serve((url) =>
      url.pathname === "/api/system/plugin/add"
        ? {
            status: 400,
            body: {
              error: "Can't add '/tmp/x': local paths aren't added, they are developed.",
            },
          }
        : base(url),
    );
    const user = userEvent.setup();
    show();
    await user.click(screen.getByRole("button", { name: "Add" }));
    await user.type(screen.getByLabelText(/A plugin's name/), "/tmp/x");
    await user.click(screen.getByRole("button", { name: "Install" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("local paths aren't added");
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(calls.filter((request) => new URL(request.url).searchParams.get("confirm"))).toHaveLength(0);
  });

  it("uses the shared confirmation before disabling and restarts through verified lifecycle", async () => {
    let restarted = false;
    const calls = serve((url) => {
      if (url.pathname === "/api/system/plugin/disable") return { body: change("disable", true) };
      if (url.pathname === "/api/restart") {
        restarted = true;
        return {
          status: 202,
          body: { restarting: true, instance: "old", message: "Restarting" },
        };
      }
      return base(url);
    });
    const reload = vi.fn();
    const user = userEvent.setup();
    show({ reload, restartInterval: 1 });
    await user.click(
      await screen.findByRole("button", {
        name: "Disable heinzel-clock",
      }),
    );
    await user.click(
      within(screen.getByRole("group", { name: /Disable/ })).getByRole("button", { name: "Restart and apply" }),
    );
    const dialog = await screen.findByRole("alertdialog", { name: "Restart and apply Plugins" });
    expect(dialog).toHaveTextContent("Disable: heinzel-clock");
    await user.click(within(dialog).getByRole("button", { name: "Restart and apply Plugins" }));
    const disabled = calls.find((request) => new URL(request.url).pathname === "/api/system/plugin/disable");
    expect(new URL(disabled?.url ?? "").searchParams.get("distribution")).toBe("heinzel-clock");

    await waitFor(() => expect(reload).toHaveBeenCalledTimes(1));
    expect(restarted).toBe(true);
  });

  it("offers Enable for a disabled plugin and links its source", async () => {
    serve((url) =>
      url.pathname === "/api/system/plugin/list" ? { body: [{ ...installed, enabled: false }] } : base(url),
    );
    show();
    expect(await screen.findByRole("button", { name: "Enable heinzel-clock" })).toBeInTheDocument();
    expect(screen.getByText("Disabled")).toBeInTheDocument();
    expect(
      screen.getByRole("link", {
        name: "github.com/helmut-hoffer-von-ankershoffen/heinzel",
      }),
    ).toHaveAttribute("href", installed.source_url);
  });
});
