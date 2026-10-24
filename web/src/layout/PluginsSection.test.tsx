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
      return Response.json(found.body, { status: found.status ?? 200 });
    }),
  );
  return calls;
}

function base(url: URL): { status?: number; body: unknown } | undefined {
  switch (url.pathname) {
    case "/api/system/plugin/list":
      return { body: [installed] };
    case "/api/system/plugin/search":
      return { body: { plugins: [], warnings: [] } };
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

beforeEach(() => sessionStorage.clear());
afterEach(() => vi.unstubAllGlobals());

describe("PluginsSection", () => {
  it("shows what a spec would install first, and installs the pinned requirement only on confirm", async () => {
    const calls = serve((url) => {
      if (url.pathname === "/api/system/plugin/add") {
        return { body: change("add", url.searchParams.get("confirm") === "true") };
      }
      return base(url);
    });
    const user = userEvent.setup();
    const { container } = show();
    await user.click(screen.getByRole("button", { name: "Add" }));
    await user.type(screen.getByLabelText(/A plugin's name/), candidate.spec);
    await user.click(screen.getByRole("button", { name: "Install" }));

    const dialog = await screen.findByRole("alertdialog");
    const adds = () => calls.filter((request) => new URL(request.url).pathname === "/api/system/plugin/add");
    expect(adds()).toHaveLength(1);
    expect(new URL(adds()[0]?.url ?? "").searchParams.get("spec")).toBe(candidate.spec);
    expect(new URL(adds()[0]?.url ?? "").searchParams.get("confirm")).toBeNull();
    for (const text of ["heinzel-crm", "1.2.0", "acme-example (Ada Example)", PINNED, candidate.warnings[0] ?? ""]) {
      expect(within(dialog).getByText(text)).toBeInTheDocument();
    }
    expect(within(dialog).getByText(/private repository/)).toBeInTheDocument();
    expect(within(dialog).getByRole("link", { name: "from github.com/acme-example/heinzel-crm" })).toHaveAttribute(
      "href",
      candidate.url,
    );
    await expectAccessible(container);

    await user.click(within(dialog).getByRole("button", { name: "Install" }));
    expect(await screen.findByText("Restart hz ui to load it.")).toBeInTheDocument();
    const confirmed = new URL(adds()[1]?.url ?? "");
    expect(confirmed.searchParams.get("spec")).toBe(PINNED);
    expect(confirmed.searchParams.get("confirm")).toBe("true");
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  it("says why a source is refused and installs nothing", async () => {
    const calls = serve((url) =>
      url.pathname === "/api/system/plugin/add"
        ? { status: 400, body: { error: "Can't add '/tmp/x': local paths aren't added, they are developed." } }
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

  it("disables an installed plugin after asking, then restarts hz ui and reloads once it answers", async () => {
    let restarted = false;
    let infoAsked = 0;
    const calls = serve((url) => {
      if (url.pathname === "/api/system/plugin/disable") return { body: change("disable", true) };
      if (url.pathname === "/api/restart") {
        restarted = true;
        return { status: 202, body: { restarting: true, message: "Restarting" } };
      }
      if (url.pathname === "/api/system/info") {
        infoAsked += 1;
        return infoAsked < 2 ? { status: 503, body: { error: "starting" } } : { body: { version: "0.3.0" } };
      }
      return base(url);
    });
    const reload = vi.fn();
    const user = userEvent.setup();
    show({ reload, restartInterval: 1 });
    await user.click(await screen.findByRole("button", { name: "Disable heinzel-clock" }));
    const dialog = screen.getByRole("alertdialog");
    expect(dialog).toHaveTextContent("Disable heinzel-clock?");
    await user.click(within(dialog).getByRole("button", { name: "Disable" }));
    const disabled = calls.find((request) => new URL(request.url).pathname === "/api/system/plugin/disable");
    expect(new URL(disabled?.url ?? "").searchParams.get("distribution")).toBe("heinzel-clock");

    await user.click(await screen.findByRole("button", { name: "Restart hz ui" }));
    await waitFor(() => expect(reload).toHaveBeenCalledTimes(1));
    expect(restarted).toBe(true);
    expect(infoAsked).toBe(2);
  });

  it("offers Enable for a disabled plugin and links its source", async () => {
    serve((url) =>
      url.pathname === "/api/system/plugin/list" ? { body: [{ ...installed, enabled: false }] } : base(url),
    );
    show();
    expect(await screen.findByRole("button", { name: "Enable heinzel-clock" })).toBeInTheDocument();
    expect(screen.getByText("Disabled")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "from github.com/helmut-hoffer-von-ankershoffen/heinzel" }),
    ).toHaveAttribute("href", installed.source_url);
  });
});
