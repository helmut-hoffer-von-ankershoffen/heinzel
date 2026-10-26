import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Providers, queryClient } from "@/App";
import { expectAccessible } from "@/test/render";
import { PluginsSection } from "./PluginsSection";

const PINNED = `heinzel-crm @ git+https://github.com/acme-example/heinzel-crm@${"b".repeat(40)}`;

const installed = {
  distribution: "heinzel-clock",
  version: "0.3.0",
  modules: ["clock"],
  name: "clock",
  summary: "What time it is for everyone on the team.",
  source_kind: "git",
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
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      calls.push(request);
      const url = new URL(request.url);
      const found = answer(url, request);
      if (!found) return Response.json({ error: `no answer for ${url.pathname}` }, { status: 404 });
      if (url.pathname.startsWith("/api/stream/")) {
        const events = [
          `event: task\ndata: ${JSON.stringify({ text: "Adding plugin heinzel-crm" })}\n\n`,
          `event: result\ndata: ${JSON.stringify({ data: found.body })}\n\n`,
        ].join("");
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
  lifecycleReads = 0;
});
afterEach(() => vi.unstubAllGlobals());

describe("PluginsSection", () => {
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

  it("installs a first-party plugin from Discover by name and a community one by its spec", async () => {
    const entry = (name: string, community: boolean, install_spec: string, source?: string) => ({
      name: `heinzel-${name}`,
      short_name: name,
      description: `The ${name} plugin.`,
      source: source ?? (community ? "github" : "first-party"),
      community,
      install_spec,
      url: `https://example.org/${name}`,
      repository_url: `https://github.com/acme-example/heinzel-${name}`,
      stars: null,
      installed: false,
    });
    const git = "git+https://github.com/acme-example/heinzel-crm";
    const calls = serve((url) => {
      if (url.pathname === "/api/system/plugin/search") {
        return {
          body: {
            plugins: [entry("zoom", false, "zoom"), entry("crm", true, git), entry("weather", true, "w", "contrib")],
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
    expect(labels.some((text) => text?.includes("Contrib"))).toBe(true);
    expect(labels.some((text) => text?.includes("Contrib"))).toBe(true);
    expect(labels.some((text) => text?.includes("First-party"))).toBe(true);
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

    await user.click((await screen.findAllByRole("button", { name: "Install" }))[1] as HTMLElement);
    expect(spec(2)).toBe(git);
    await user.click(
      within(await screen.findByRole("group", { name: /Install/ })).getByRole("button", { name: "Restart and apply" }),
    );
    await user.click(await screen.findByRole("button", { name: "Restart and apply Plugins" }));
    await waitFor(() => expect(adds()).toHaveLength(4));
    expect(spec(3)).toBe(PINNED);
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
