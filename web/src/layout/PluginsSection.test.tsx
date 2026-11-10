import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Providers, queryClient } from "@/App";
import { OperationDialog } from "@/components/OperationDialog";
import { getRun, resetOperationRun } from "@/lib/operationRun";
import { expectAccessible } from "@/test/render";
import { CandidateDetails, ModuleHeader, PluginsSection, sourceLabel } from "./PluginsSection";

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
  installed_version: null as string | null,
  warnings: ["It is named crm-tools, not heinzel-<name> as plugins are (ADR 0025)."],
  also: "",
};
let receiptCount = 0;
let confirmedStreamDelay = 0;
let restartDelay = 0;
let phaseEvents = "";

describe("plugin source labels", () => {
  it.each([
    ["https://github.com/helmut-hoffer-von-ankershoffen/heinzel/tree/main/plugins/heinzel-clock", null, "First-party"],
    ["https://github.com/HELMUT-HOFFER-VON-ANKERSHOFFEN/HEINZEL.git", null, "First-party"],
    [
      "https://github.com/helmut-hoffer-von-ankershoffen/heinzel-plugins/tree/main/plugins/heinzel-clock",
      null,
      "First-party",
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
    expect(screen.queryByText("First-party")).not.toBeInTheDocument();
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
      if (url.pathname === "/api/restart" && restartDelay > 0)
        await new Promise((resolve, reject) => {
          const timer = setTimeout(resolve, restartDelay);
          request.signal.addEventListener("abort", () => {
            clearTimeout(timer);
            reject(new DOMException("aborted", "AbortError"));
          });
        });
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
        const events = [task, url.searchParams.get("confirm") === "true" ? phaseEvents : "", result].join("");
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
      <OperationDialog />
    </Providers>,
  );
}

/** The one operation dialog, by its title. */
const dialog = (name: RegExp) => screen.findByRole("dialog", { name });

/** Choose `action` in the dialog titled `name`: the one confirmation a plugin change asks. */
async function choose(user: ReturnType<typeof userEvent.setup>, name: RegExp, action: string) {
  await user.click(within(await dialog(name)).getByRole("button", { name: action }));
}

/** No inline confirmation and no second restart question ever renders. */
function expectNoOldConfirmation() {
  expect(screen.queryByRole("group", { name: /Install this plugin/ })).toBeNull();
  expect(document.querySelector("[data-plugin-confirm]")).toBeNull();
  expect(screen.queryByText(/Restart and apply/)).toBeNull();
  expect(screen.queryByText(/Restart the server to apply/)).toBeNull();
}

beforeEach(() => {
  window.history.replaceState(null, "", "/");
  sessionStorage.clear();
  receiptCount = 0;
  confirmedStreamDelay = 0;
  restartDelay = 0;
  phaseEvents = "";
  lifecycleReads = 0;
});
afterEach(() => vi.unstubAllGlobals());

describe("PluginsSection", () => {
  it("says outright which installed version a plugin replaces, without opening the details", () => {
    render(<CandidateDetails candidate={{ ...candidate, source: "git", installed_version: "0.9.3" }} />);
    const line = screen.getByText(/Replaces 0\.9\.3/);
    expect(line.closest("details")).toBeNull();
  });

  it("says before it starts which plugins it installs with this one", () => {
    const also = "heinzel-crm also needs heinzel-github; installing it too.";
    render(<CandidateDetails candidate={{ ...candidate, source: "git", also }} />);
    expect(screen.getByTestId("plugin-also").textContent).toBe(also);
    expect(screen.getByText(also).closest("details")).toBeNull();
  });

  it("lists the plugins it also installs, with versions, and nothing for a plugin alone", () => {
    const need = { required: true, needed_by: "x", install: "acme", detail: "", with_it: true };
    const needs = [
      {
        ...need,
        distribution: "heinzel-acme",
        requirement: "heinzel-acme @ git+x",
        state: "install" as const,
        version: "0.2.0",
      },
      {
        ...need,
        distribution: "heinzel-acme-payments",
        requirement: "p @ git+x",
        state: "install" as const,
        version: null,
      },
      {
        ...need,
        distribution: "heinzel-google",
        requirement: "heinzel-google",
        state: "offered" as const,
        required: false,
      },
    ];
    const { unmount } = render(
      <CandidateDetails
        candidate={{ ...candidate, source: "git", needs, also: "x also needs heinzel-acme; installing it too." }}
      />,
    );
    const list = screen.getByTestId("plugin-also-installs");
    expect(screen.getByTestId("plugin-also")).toHaveTextContent("also needs heinzel-acme; installing it too.");
    expect(list).toHaveTextContent("heinzel-acme 0.2.0");
    expect(list).toHaveTextContent("heinzel-acme-payments");
    expect(list).not.toHaveTextContent("heinzel-google");
    unmount();
    render(<CandidateDetails candidate={{ ...candidate, source: "git", needs: [] }} />);
    expect(screen.queryByTestId("plugin-also")).toBeNull();
  });

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
    await choose(user, /Install heinzel-crm/, "Install");
    expect(
      (await screen.findAllByRole("alert")).some((alert) =>
        alert.textContent?.includes("cannot confirm how this installation ended"),
      ),
    ).toBe(true);
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

    await choose(user, /Install heinzel-crm/, "Install");
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
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Install" })).not.toBeInTheDocument();
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
    await choose(user, /Install heinzel-crm/, "Install");
    await choose(user, /Installed heinzel-crm/, "Done");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
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

  it("shows a confirmed install's real steps, each plugin it needs among them, in the operation dialog", async () => {
    const phase = (id: string, label: string, state: string, detail?: string) =>
      `event: phase\ndata: ${JSON.stringify({ id, label, state, ...(detail ? { detail } : {}) })}\n\n`;
    phaseEvents = [
      phase("install", "Installing heinzel-crm", "pending"),
      phase("install", "Installing heinzel-crm", "done"),
      phase("verify", "Checking that exactly it is installed", "done"),
      phase("needs:heinzel-auth", "Plugin heinzel-auth, which heinzel-crm needs", "done", "Installed with it: 1.0.0"),
      phase("record", "Recording the add", "done"),
    ].join("");
    let done = false;
    const crm = { ...installed, distribution: "heinzel-crm", name: "crm", version: "1.2.0", summary: "CRM notes." };
    serve((url) => {
      // The final check reads the list fresh: the confirmed install is in it.
      if (url.pathname === "/api/system/plugin/list") return { body: done ? [installed, crm] : [installed] };
      if (url.pathname === "/api/system/plugin/add" || url.pathname === "/api/stream/system/plugin/add") {
        done = url.searchParams.get("confirm") === "true";
        return {
          body: change("add", done, { plugins: done ? [installed, crm] : [installed], restart_required: false }),
        };
      }
      return base(url);
    });
    const user = userEvent.setup();
    show();
    await user.click(screen.getByRole("button", { name: "Add" }));
    await user.type(screen.getByLabelText(/A plugin's name/), candidate.spec);
    await user.click(screen.getByRole("button", { name: "Install" }));
    await choose(user, /Install heinzel-crm/, "Install");
    await waitFor(() => expect(getRun().stage).toBe("done"));
    const run = getRun();
    if (run.stage !== "done") throw new Error("not done");
    expect(run.call.path).toBe("/system/plugin/add");
    // The plan is fixed before it starts; the server's own steps (its check, each plugin it needs) are the write's
    // detail. The install took effect without a restart, so restarting, waiting and reloading were not needed.
    expect(run.phases.map(({ id, state }) => [id, state])).toEqual([
      ["preserve", "done"],
      ["write", "done"],
      ["restart", "skipped"],
      ["wait", "skipped"],
      ["reload", "skipped"],
      ["check", "done"],
    ]);
    expect(run.phases[1]?.label).toBe("Installing heinzel-crm");
    expect(run.phases[1]?.detail).toBe(
      "Checked · Plugin heinzel-auth, which heinzel-crm needs: Installed with it: 1.0.0",
    );
    const shown = await dialog(/Installed heinzel-crm/);
    expect(
      within(shown).getByText(/Plugin heinzel-auth, which heinzel-crm needs: Installed with it: 1\.0\.0/),
    ).toBeInTheDocument();
  });

  it("restarts once and reloads after a Discover install whose row leaves the list", async () => {
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
              {
                name: "heinzel-other",
                short_name: "other",
                description: "Another plugin.",
                source: "github",
                install_spec: "heinzel-other",
                installed: false,
                community: true,
              },
            ],
            warnings: [],
          },
        };
      if (url.pathname === "/api/system/plugin/add" || url.pathname === "/api/stream/system/plugin/add") {
        done = url.searchParams.get("confirm") === "true";
        return { body: change("add", done, { plugins: done ? [installed, crm] : [installed] }) };
      }
      return base(url);
    });
    // The restart request takes a moment, as over a real connection, while the installed row leaves Discover.
    restartDelay = 50;
    const reload = vi.fn();
    const user = userEvent.setup();
    show({ reload, restartInterval: 1 });
    await user.click(screen.getByRole("tab", { name: "Discover" }));
    await user.click((await screen.findAllByRole("button", { name: "Install" }))[0] as HTMLElement);
    await choose(user, /Install heinzel-crm/, "Install");
    await waitFor(() => expect(reload).toHaveBeenCalledOnce());
    expect(screen.queryByText(/may already be restarting/)).not.toBeInTheDocument();
    expect(calls.filter((call) => new URL(call.url).pathname === "/api/restart")).toHaveLength(1);
    expect(calls.filter((call) => new URL(call.url).searchParams.get("confirm") === "true")).toHaveLength(1);
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
    await choose(user, /Install heinzel-crm/, "Install");
    const failed = await dialog(/Installing heinzel-crm/);
    expect(await within(failed).findByRole("alert")).toHaveTextContent("Restart refused fixture");
    expect(screen.getByText("CRM", { exact: true })).toBeInTheDocument();
    // Trying again observes the kept restart; it never offers the install itself again.
    expect(within(failed).getByRole("button", { name: "Try again" })).toBeInTheDocument();
    expect(within(failed).queryByRole("button", { name: "Install" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Restart and apply" })).not.toBeInTheDocument();
    expect(calls.filter((call) => new URL(call.url).searchParams.get("confirm") === "true")).toHaveLength(1);
    expect(calls.filter((call) => new URL(call.url).pathname === "/api/restart")).toHaveLength(1);

    const savedRestart = JSON.parse(sessionStorage.getItem("hz.plugin-add-restart.v1") ?? "null");
    expect(savedRestart.observation.retired).toContain("old");
    expect(savedRestart.acknowledged).toBe(false);
    view.unmount();
    // A reload starts with no run in memory; only what the tab stored survives it.
    resetOperationRun();
    show({ reload, restartInterval: 1 });
    expect(await screen.findByRole("alert")).toHaveTextContent("can’t verify the previous installation or restart");
    expect(screen.queryByRole("button", { name: "Restart and apply" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Check again" })).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(reload).not.toHaveBeenCalled();
    expect(calls.filter((call) => new URL(call.url).pathname === "/api/restart")).toHaveLength(1);
    expect(calls.filter((call) => new URL(call.url).searchParams.get("confirm") === "true")).toHaveLength(1);
  });

  it("restores a confirmed restart receipt and skips every retiring instance", async () => {
    const operationId = "previous-operation";
    sessionStorage.setItem("hz.plugin-add-operation.v1", operationId);
    sessionStorage.setItem(
      "hz.plugin-add-restart.v1",
      JSON.stringify({
        operation_id: operationId,
        acknowledged: true,
        observation: { retired: ["old", "intermediate"], version: "0.9.23", settings: [] },
      }),
    );
    let reads = 0;
    const calls = serve((url) => {
      if (url.pathname === "/api/system/plugin/operation")
        return { body: { operation_id: operationId, status: "completed", candidate, result: change("add", true) } };
      if (url.pathname === "/api/lifecycle")
        return {
          body: { instance: reads++ === 0 ? "intermediate" : "replacement", version: "0.9.23", restartable: true },
        };
      if (url.pathname === "/api/lifecycle/verify")
        return { body: { instance: "replacement", version: "0.9.23", matches: true, mismatched: [] } };
      return base(url);
    });
    const reload = vi.fn();
    show({ reload, restartInterval: 1 });
    // The dialog opens in its progress view at the restart it observes again: it never asks a second time.
    const resumed = await dialog(/Installing heinzel-crm/);
    expect(within(resumed).queryByRole("button", { name: "Install" })).not.toBeInTheDocument();
    expect(within(resumed).queryByRole("button", { name: "Cancel" })).not.toBeInTheDocument();
    await waitFor(() => expect(reload).toHaveBeenCalledOnce());
    const verifies = calls.filter((request) => new URL(request.url).pathname === "/api/lifecycle/verify");
    expect(verifies).toHaveLength(1);
    await expect(verifies[0]?.clone().json()).resolves.toMatchObject({ instance: "replacement" });
    expect(calls.filter((call) => new URL(call.url).pathname === "/api/restart")).toHaveLength(0);
    expect(calls.filter((call) => new URL(call.url).searchParams.get("confirm") === "true")).toHaveLength(0);
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
    // The confirmed install answers after a moment, so its running view can be read.
    confirmedStreamDelay = 300;
    show({ reload, restartInterval: 1 });
    await user.click(screen.getByRole("button", { name: "Add" }));
    await user.type(screen.getByLabelText(/A plugin's name/), candidate.spec);
    await user.click(screen.getByRole("button", { name: "Install" }));

    const preview = await dialog(/Install heinzel-crm/);
    expectNoOldConfirmation();
    // One question: Cancel, then Install, beside the title row's Close; the dialog opens on Cancel.
    expect(
      within(preview)
        .getAllByRole("button")
        .map((button) => button.getAttribute("aria-label") ?? button.textContent?.trim()),
    ).toEqual(["Close", "Cancel", "Install"]);
    expect(within(preview).getByRole("button", { name: "Cancel" })).toHaveFocus();
    for (const line of [
      "Runs as code on this computer with your permissions.",
      "hz restarts and this page reloads itself.",
      "Your open work is preserved.",
    ]) {
      expect(within(preview).getByText(line)).toBeInTheDocument();
    }
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
    await expectAccessible(document.body);

    expect(adds()).toHaveLength(1);
    await user.click(within(preview).getByRole("button", { name: "Install" }));
    const running = await dialog(/Installing heinzel-crm/);
    expect(within(running).getByText(/Step \d of 6: /)).toBeInTheDocument();
    expect(within(running).getByRole("progressbar")).toBeInTheDocument();
    const steps = within(within(running).getByRole("list", { name: /steps/i })).getAllByRole("listitem");
    const plan = [
      "Preserving your open work",
      "Installing heinzel-crm",
      "Restarting hz",
      "Waiting for hz to come back",
      "Reloading the page",
      "Checking that exactly it is installed",
    ];
    expect(steps).toHaveLength(plan.length);
    expect(steps.map((step, index) => step.textContent?.startsWith(plan[index] ?? "-"))).toEqual(plan.map(() => true));
    expect(within(running).queryByRole("button", { name: "Cancel" })).not.toBeInTheDocument();
    expect(within(running).queryByRole("button", { name: "Close" })).not.toBeInTheDocument();
    expect(within(running).getByRole("button", { name: "Installing…" })).toBeDisabled();
    expectNoOldConfirmation();
    await waitFor(() => expect(reload).toHaveBeenCalledTimes(1));
    expect(calls.some((request) => new URL(request.url).pathname === "/api/restart")).toBe(true);
    const confirmed = new URL(adds()[1]?.url ?? "");
    expect(confirmed.pathname).toBe("/api/stream/system/plugin/add");
    expect(confirmed.searchParams.get("spec")).toBe(PINNED);
    expect(confirmed.searchParams.get("confirm")).toBe("true");
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    // The reloaded page opens the dialog again at its final check.
    const saved = JSON.parse(sessionStorage.getItem("hz.plugin-operation.v1") ?? "null");
    expect(saved?.pending?.action).toBe("add");
    expect(saved?.phases?.find((phase: { id: string }) => phase.id === "reload")?.state).toBe("running");
    await choose(user, /Installed heinzel-crm/, "Done");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expectNoOldConfirmation();
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
      await choose(
        user,
        action === "add" ? /Install heinzel-crm/ : /Remove heinzel-clock\?/,
        action === "add" ? "Install" : "Remove",
      );

      // While the stream stays open past four seconds, the dialog shows its write step running.
      const running = await dialog(action === "add" ? /Installing heinzel-crm/ : /Removing heinzel-clock/);
      expect(
        await within(running).findByText(
          `Step 2 of 6: ${action === "add" ? "Installing heinzel-crm" : "Removing heinzel-clock"}`,
        ),
      ).toBeInTheDocument();
      await waitFor(() => expect(reload).toHaveBeenCalledOnce(), { timeout: 10_000 });
      const paths = calls.map((request) => new URL(request.url).pathname);
      expect(paths.filter((path) => path === `/api/stream/system/plugin/${action}`)).toHaveLength(1);
      expect(paths.filter((path) => path === "/api/system/plugin/remove")).toHaveLength(0);
      expect(paths.filter((path) => path === "/api/restart")).toHaveLength(1);
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    },
  );

  it("still restarts when the removed plugin's row has already left the refreshed list", async () => {
    let removed = false;
    const calls = serve((url) => {
      if (url.pathname === "/api/stream/system/plugin/remove") {
        removed = true;
        return { body: change("remove", true, { distribution: "heinzel-clock", plugins: [] }) };
      }
      if (url.pathname === "/api/system/plugin/list" && removed) return { body: [] };
      return base(url);
    });
    // The list answers first and the lifecycle read that precedes the restart later, as on a real server.
    const answer = vi.mocked(fetch).getMockImplementation();
    if (!answer) throw new Error("missing fetch fixture");
    vi.mocked(fetch).mockImplementation(async (request) => {
      if (removed && new URL((request as Request).url).pathname.startsWith("/api/lifecycle")) {
        await new Promise((resolve) => setTimeout(resolve, 200));
      }
      return answer(request);
    });
    const user = userEvent.setup();
    const reload = vi.fn();
    show({ reload, restartInterval: 1 });
    await user.click(await screen.findByRole("button", { name: "Remove heinzel-clock" }));
    await choose(user, /Remove heinzel-clock\?/, "Remove");
    await waitFor(() => expect(reload).toHaveBeenCalledOnce(), { timeout: 10_000 });
    expect(calls.filter((call) => new URL(call.url).pathname === "/api/restart")).toHaveLength(1);
  });

  it("does not install a previewed plugin when confirming a different action", async () => {
    let removed = false;
    const calls = serve((url) => {
      if (url.pathname.endsWith("/plugin/add")) return { body: change("add", url.searchParams.has("confirm")) };
      if (url.pathname === "/api/stream/system/plugin/remove") {
        removed = true;
        return { body: change("remove", true, { distribution: "heinzel-clock", restart_required: false }) };
      }
      if (url.pathname === "/api/system/plugin/list" && removed) return { body: [] };
      return base(url);
    });
    const answer = vi.mocked(fetch).getMockImplementation();
    if (!answer) throw new Error("missing fetch fixture");
    let markReceiptStarted!: () => void;
    let releaseReceipt!: () => void;
    const receiptStarted = new Promise<void>((resolve) => {
      markReceiptStarted = resolve;
    });
    const receiptReady = new Promise<void>((resolve) => {
      releaseReceipt = resolve;
    });
    vi.mocked(fetch).mockImplementation(async (request) => {
      const url = new URL((request as Request).url);
      if (url.pathname === "/api/system/plugin/operation") {
        markReceiptStarted();
        await receiptReady;
      }
      return answer(request);
    });
    show({ restartInterval: 1 });
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Add" }));
    await user.type(screen.getByLabelText(/A plugin's name/), candidate.spec);
    await user.click(screen.getByRole("button", { name: "Install" }));
    await receiptStarted;

    const remove = await screen.findByRole("button", { name: "Remove heinzel-clock" });
    expect(remove).toBeDisabled();
    releaseReceipt();
    await choose(user, /Install heinzel-crm/, "Cancel");
    await waitFor(() => expect(remove).toBeEnabled());
    await user.click(remove);
    await choose(user, /Remove heinzel-clock\?/, "Remove");
    await dialog(/Removed heinzel-clock/);

    const writes = () =>
      calls
        .map((call) => new URL(call.url))
        .filter((url) => url.pathname.endsWith("/plugin/remove") || url.searchParams.get("confirm") === "true")
        .map((url) => url.pathname);
    expect(writes()).toEqual(["/api/stream/system/plugin/remove"]);
  });

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
    await choose(user, /Remove heinzel-clock\?/, "Remove");
    expect(await screen.findAllByText("response lost")).not.toHaveLength(0);
    // Trying again in the same dialog is the same confirmation: the lost removal is not sent again.
    await choose(user, /Removing heinzel-clock/, "Try again");
    expect(await screen.findAllByText(/The request result is unknown/)).not.toHaveLength(0);
    expect(calls.filter((call) => new URL(call.url).pathname === "/api/stream/system/plugin/remove")).toHaveLength(1);
    expect(calls.filter((call) => new URL(call.url).pathname === "/api/restart")).toHaveLength(0);
    // Reopening the still-present plugin is a new explicit choice after the invalidated list has been read.
    // The failed dialog's footer Close (the title row has one too).
    await user.click(
      within(await dialog(/Removing heinzel-clock/))
        .getAllByRole("button", { name: "Close" })
        .at(-1) as HTMLElement,
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await user.click(await screen.findByRole("button", { name: "Remove heinzel-clock" }));
    await choose(user, /Remove heinzel-clock\?/, "Remove");
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
    // Each restart brings up a new server instance, so both installs can finish.
    let restarts = 0;
    const calls = serve((url) => {
      if (url.pathname === "/api/restart") restarts += 1;
      if (url.pathname === "/api/lifecycle")
        return { body: { instance: `instance-${restarts}`, version: "0.3.0", restartable: true } };
      if (url.pathname === "/api/lifecycle/verify")
        return { body: { instance: `instance-${restarts}`, version: "0.3.0", matches: true, mismatched: [] } };
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
    expect(labels.some((text) => text?.includes("First-party"))).toBe(true);
    const row = (name: string) => screen.getByText(name, { exact: true }).closest("li") as HTMLElement;
    expect(within(row("Reference")).getByText("Custom")).toBeInTheDocument();
    expect(within(row("Private Tools")).getByText("Private")).toBeInTheDocument();
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
    await choose(user, /Install heinzel-crm/, "Install");
    await waitFor(() => expect(adds()).toHaveLength(2));
    expect(spec(1)).toBe("zoom");

    await choose(user, /Installed heinzel-crm/, "Done");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await user.click(screen.getByRole("tab", { name: "Discover" }));

    await user.click((await screen.findAllByRole("button", { name: "Install" }))[1] as HTMLElement);
    expect(spec(2)).toBe(git);
    await choose(user, /Install heinzel-crm/, "Install");
    await waitFor(() => expect(adds()).toHaveLength(4));
    expect(spec(3)).toBe(PINNED);
    // The second install runs to its end here: a run outlives the section, so it must not leak into the next test.
    await waitFor(() => expect(getRun().stage).toBe("done"));
    expect(screen.getByRole("dialog", { name: /Installed heinzel-crm/ })).toBeInTheDocument();
  });

  it("opens the confirmation for the clicked Discover row in the dialog, focused, one at a time, with provenance collapsed", async () => {
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
    const card = await dialog(/Install heinzel-crm/);
    expect(screen.getAllByRole("dialog")).toHaveLength(1);
    expectNoOldConfirmation();
    expect(within(card).getByRole("button", { name: "Cancel" })).toHaveFocus();
    expect(within(row).getByRole("button", { name: "Install" })).toHaveAttribute("aria-expanded", "true");
    expect(card.querySelector("details")).not.toHaveAttribute("open");
    expect(screen.getByRole("tab", { name: "Installed" })).toBeDisabled();
    // Another row's Install does not open a second confirmation.
    await user.click(within(rows[0] as HTMLElement).getByRole("button", { name: "Install" }));
    expect(screen.getAllByRole("dialog")).toHaveLength(1);
    await user.click(within(card).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Installed" })).toBeEnabled();
  });

  it("refreshes Discover past the server's cache, and only there", async () => {
    window.history.replaceState(null, "", "/");
    const calls = serve((url) =>
      url.pathname === "/api/system/plugin/search" ? { body: { plugins: [], warnings: [] } } : base(url),
    );
    const user = userEvent.setup();
    show({ restartInterval: 1 });
    expect(screen.queryByRole("button", { name: "Refresh" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Discover" }));
    const refresh = await screen.findByRole("button", { name: "Refresh" });
    expect(refresh).toHaveAttribute("title", "Refresh");
    const searches = () => calls.filter((call) => new URL(call.url).pathname === "/api/system/plugin/search");
    expect(searches().every((call) => !new URL(call.url).searchParams.has("refresh"))).toBe(true);
    await user.click(refresh);
    await waitFor(() =>
      expect(searches().some((call) => new URL(call.url).searchParams.get("refresh") === "true")).toBe(true),
    );
  });

  it("keeps the tab in the address so a reload returns to it", async () => {
    window.history.replaceState(null, "", "/");
    serve((url) =>
      url.pathname === "/api/system/plugin/search" ? { body: { plugins: [], warnings: [] } } : base(url),
    );
    const user = userEvent.setup();
    const first = show({ restartInterval: 1 });
    await user.click(screen.getByRole("tab", { name: "Discover" }));
    expect(window.location.search).toBe("?plugins=discover");
    first.unmount();
    show({ restartInterval: 1 });
    expect(screen.getByRole("tab", { name: "Discover" })).toHaveAttribute("aria-selected", "true");
    await user.click(screen.getByRole("tab", { name: "Installed" }));
    expect(window.location.search).toBe("");
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
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
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
    const asked = await dialog(/Disable heinzel-clock/);
    expect(asked).toHaveTextContent("It stays installed, switched off.");
    expect(asked).toHaveTextContent("hz restarts and this page reloads itself.");
    expect(
      within(asked)
        .getAllByRole("button")
        .map((button) => button.getAttribute("aria-label") ?? button.textContent?.trim()),
    ).toEqual(["Close", "Cancel", "Disable"]);
    expect(calls.some((request) => new URL(request.url).pathname === "/api/system/plugin/disable")).toBe(false);
    await user.click(within(asked).getByRole("button", { name: "Disable" }));
    expectNoOldConfirmation();
    await waitFor(() =>
      expect(calls.some((request) => new URL(request.url).pathname === "/api/system/plugin/disable")).toBe(true),
    );
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
