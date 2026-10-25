import { render, screen, within } from "@testing-library/react";
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
};

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

  it("says so when nothing is found", async () => {
    show({ servers: [], skills: [], connectors: 0, skipped: [], next: [] });
    expect(await screen.findByText("No MCP servers found.")).toBeInTheDocument();
    expect(screen.getByText("No skills found.")).toBeInTheDocument();
  });
});
