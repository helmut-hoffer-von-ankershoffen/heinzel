import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Providers, queryClient } from "@/App";
import { expectAccessible } from "@/test/render";
import { FromClaudeSection } from "./FromClaudeSection";

const scan = {
  servers: [
    {
      id: "code-user:tickets",
      name: "tickets",
      source: "code-user",
      origin: null,
      scope: "user",
      enabled: true,
      transport: "http",
      command: null,
      arguments: 0,
      host: "https://tickets.example.com",
      auth: "header",
      env_keys: [],
      header_keys: ["Authorization"],
      status: "found",
      file: "~/.claude.json",
      fingerprint: "abc",
    },
    {
      id: "code-plugin:tools:db",
      name: "db",
      source: "code-plugin",
      origin: "tools",
      scope: "plugin",
      enabled: true,
      transport: "stdio",
      command: "uvx",
      arguments: 2,
      host: null,
      auth: "env",
      env_keys: ["DB_URL"],
      header_keys: [],
      status: "found",
      file: "~/.claude/plugins/cache/tools/.mcp.json",
      fingerprint: "def",
    },
  ],
  skills: [
    {
      id: "code-user:review",
      name: "review",
      description: "Reviews code.",
      source: "code-user",
      origin: null,
      frontmatter_keys: ["description", "name"],
      files: { scripts: 2 },
      path: "~/.claude/skills/review",
      fingerprint: "ghi",
    },
  ],
  connectors: 3,
  skipped: [{ path: "~/.claude/skills/stolen", reason: "a link leaves its directory" }],
  next: [],
  notice: null,
  state_problem: null,
};

/** A server that answers the list from `state` and applies enable, disable and acknowledge to it. */
function serve(state: Record<string, unknown> & { servers: { id: string; enabled: boolean }[] }) {
  sessionStorage.setItem("hz.token", "test-token");
  const calls: string[] = [];
  const fetch = vi.fn(async (input: Request | string | URL) => {
    const url = new URL(input instanceof Request ? input.url : String(input), "http://localhost");
    const operation = url.pathname.split("/").at(-1) as string;
    expect(url.search).toBe(""); // an id can name a project folder: it travels in the body, never in the address
    const body = input instanceof Request && input.method === "POST" ? await input.clone().json() : {};
    if (operation !== "list") calls.push(`${operation} ${JSON.stringify(body)}`);
    if (operation === "acknowledge") {
      state = { ...state, notice: null };
      return Response.json({ servers: 2 });
    }
    if (operation === "enable" || operation === "disable") {
      const on = operation === "enable";
      state = { ...state, servers: state.servers.map((s) => (s.id === body.server ? { ...s, enabled: on } : s)) };
      return Response.json({ id: body.server, name: "db", scope: "plugin", enabled: on, changed: true });
    }
    return Response.json(state);
  });
  vi.stubGlobal("fetch", fetch);
  render(
    <Providers client={queryClient()}>
      <FromClaudeSection />
    </Providers>,
  );
  return calls;
}

function show(body: unknown) {
  sessionStorage.setItem("hz.token", "test-token");
  const fetch = vi.fn(async () => Response.json(body));
  vi.stubGlobal("fetch", fetch);
  return render(
    <Providers client={queryClient()}>
      <FromClaudeSection />
    </Providers>,
  );
}

beforeEach(() => sessionStorage.clear());
afterEach(() => vi.unstubAllGlobals());

describe("FromClaudeSection", () => {
  it("lists servers and skills by where they were found, with names of secrets only", async () => {
    const { container } = show(scan);
    const user = (await screen.findAllByRole("region", { name: "Claude Code, you" })).at(0) as HTMLElement;
    expect(within(user).getByText("tickets")).toBeInTheDocument();
    expect(within(user).getByText("Given: Authorization")).toBeInTheDocument();
    const plugin = screen.getByRole("region", { name: "Claude Code, plugin · tools" });
    expect(within(plugin).getByText("Given: DB_URL")).toBeInTheDocument();
    expect(screen.getByText("Reviews code.")).toBeInTheDocument();
    expect(screen.getByText("Bundled: scripts 2")).toBeInTheDocument();
    expect(screen.getByText(/3 claude.ai connectors/)).toBeInTheDocument();
    expect(screen.getByText("a link leaves its directory", { exact: false })).toBeInTheDocument();
    await expectAccessible(container);
  });

  it("shows each server's scope and switches one off and on again", async () => {
    const calls = serve(scan);
    const plugin = await screen.findByRole("region", { name: "Claude Code, plugin · tools" });
    expect(within(plugin).getByText("From a plugin")).toBeInTheDocument();
    expect(screen.getByText("Yours")).toBeInTheDocument();
    const toggle = within(plugin).getByRole("switch", { name: "db" });
    expect(toggle).toBeChecked();
    await userEvent.click(toggle);
    await waitFor(() => expect(within(plugin).getByRole("switch", { name: "db" })).not.toBeChecked());
    expect(screen.getByRole("switch", { name: "tickets" })).toBeChecked();
    await userEvent.click(within(plugin).getByRole("switch", { name: "db" }));
    await waitFor(() => expect(within(plugin).getByRole("switch", { name: "db" })).toBeChecked());
    expect(calls).toEqual(['disable {"server":"code-plugin:tools:db"}', 'enable {"server":"code-plugin:tools:db"}']);
  });

  it("gives hz itself no switch", async () => {
    const own = {
      ...(scan.servers[0] as (typeof scan.servers)[number]),
      id: "code-user:mine",
      name: "mine",
      status: "self",
      enabled: false,
    };
    serve({ ...scan, servers: [own] });
    expect(await screen.findByText("hz itself")).toBeInTheDocument();
    expect(screen.queryByRole("switch")).not.toBeInTheDocument();
  });

  it("says that found servers are on, marks the new ones, and closes the notice", async () => {
    const calls = serve({
      ...scan,
      notice: { servers: ["code-plugin:tools:db", "code-user:tickets"], project: 0, plugin: 1 },
    });
    const notice = await screen.findByRole("region", { name: "Servers switched on" });
    expect(within(notice).getByText(/2 MCP servers found in Claude are switched on/)).toBeInTheDocument();
    expect(within(notice).getByText(/From projects: 0. From plugins: 1/)).toBeInTheDocument();
    expect(screen.getAllByText("New")).toHaveLength(2);
    await userEvent.click(within(notice).getByRole("button", { name: "Got it" }));
    await waitFor(() => expect(screen.queryByRole("region", { name: "Servers switched on" })).not.toBeInTheDocument());
    expect(screen.queryByText("New")).not.toBeInTheDocument();
    expect(calls).toEqual(['acknowledge {"servers":["code-plugin:tools:db","code-user:tickets"]}']);
  });

  it("shows the server's reason when a switch is refused, and says when every server is off", async () => {
    sessionStorage.setItem("hz.token", "test-token");
    const problem = "servers.json cannot be read, so every server is switched off.";
    const off = { ...scan, state_problem: problem, servers: scan.servers.map((s) => ({ ...s, enabled: false })) };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: Request | string | URL) =>
        String(input instanceof Request ? input.url : input).includes("/list")
          ? Response.json(off)
          : Response.json({ error: "every server stays switched off" }, { status: 400 }),
      ),
    );
    render(
      <Providers client={queryClient()}>
        <FromClaudeSection />
      </Providers>,
    );
    expect(await screen.findByText(problem)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("switch", { name: "db" }));
    expect(await screen.findByText(/every server stays switched off/)).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "db" })).not.toBeChecked();
  });

  it("says so when nothing is found", async () => {
    show({ servers: [], skills: [], connectors: 0, skipped: [], next: [], notice: null, state_problem: null });
    expect(await screen.findByText("No MCP servers found.")).toBeInTheDocument();
    expect(screen.getByText("No skills found.")).toBeInTheDocument();
  });
});
