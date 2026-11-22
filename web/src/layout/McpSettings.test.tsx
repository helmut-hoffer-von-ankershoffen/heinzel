import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it } from "vitest";
import { config, shell } from "@/test/fixtures";
import { expectAccessible, mockApi } from "@/test/render";
import { renderAt } from "@/test/renderApp";

const token = {
  id: "ab12cd34",
  actor: "claude-code",
  scope: "write",
  created: "2026-11-14T10:00:00Z",
  expires: "2027-02-12T10:00:00Z",
  last_used: null,
};

it("shows one MCP switch even when the configuration schema includes it", async () => {
  mockApi({
    ...shell,
    "/api/system/config/show": {
      ...config,
      settings: [
        ...config.settings,
        {
          section: "hz",
          key: "mcp_http",
          value: false,
          default: false,
          source: "default",
          env: "HZ_MCP_HTTP",
          description: "Serve MCP to other agents.",
          json_schema: { type: "boolean", title: "MCP for other agents" },
        },
      ],
    },
    "/api/_web/mcp": { enabled: false, tokens: [] },
  });
  const user = userEvent.setup();
  renderAt("/doctor?settings=mcp");
  const dialog = await screen.findByRole("dialog", { name: "Settings" });
  expect(await within(dialog).findAllByRole("switch")).toHaveLength(1);
  expect(within(dialog).getByRole("switch", { name: "Serve MCP to other agents" })).toBeInTheDocument();
  await user.type(within(dialog).getByRole("searchbox"), "mcp");
  expect(within(dialog).queryByRole("switch")).not.toBeInTheDocument();
  await user.click(within(dialog).getAllByRole("button", { name: "MCP" })[1]);
  expect(await within(dialog).findAllByRole("switch")).toHaveLength(1);
});

it("switches MCP on, makes a token shown once with copy buttons, and revokes it", async () => {
  let state = { enabled: false, tokens: [] as (typeof token)[] };
  const { calls } = mockApi({
    ...shell,
    "/api/_web/mcp": async (request: Request) => {
      if (request.method === "PUT")
        state = { ...state, enabled: ((await request.json()) as { enabled: boolean }).enabled };
      return state;
    },
    "/api/_web/mcp/tokens": () => {
      state = { ...state, tokens: [token] };
      return { ...token, token: "hzmcp_secret-shown-once" };
    },
    "/api/_web/mcp/tokens/ab12cd34": () => {
      state = { ...state, tokens: [] };
      return { revoked: [token] };
    },
  });
  const user = userEvent.setup();
  const { container } = renderAt("/doctor?settings=mcp");
  const dialog = await screen.findByRole("dialog", { name: "Settings" });
  expect(await within(dialog).findByText("MCP is off. Nothing answers at /mcp.")).toBeInTheDocument();
  expect(within(dialog).queryByText("Tokens")).not.toBeInTheDocument();
  await user.click(within(dialog).getByRole("switch", { name: "Serve MCP to other agents" }));
  expect(await within(dialog).findByText("Tokens")).toBeInTheDocument();
  expect(calls.find((request) => request.method === "PUT")?.headers.get("Authorization")).toBe("Bearer test-token");
  await user.type(within(dialog).getByLabelText("Agent name"), "claude-code");
  await user.selectOptions(within(dialog).getByLabelText("May"), "write");
  await user.click(within(dialog).getByRole("button", { name: "Make token" }));
  expect(await within(dialog).findByText("hzmcp_secret-shown-once")).toBeInTheDocument();
  expect(within(dialog).getByRole("button", { name: "Copy token" })).toBeInTheDocument();
  expect(within(dialog).getByRole("button", { name: "Copy the Claude Code command" })).toBeInTheDocument();
  expect(dialog).toHaveTextContent(
    `claude mcp add --transport http hz ${window.location.origin}/mcp --header "Authorization: Bearer hzmcp_secret-shown-once"`,
  );
  await expectAccessible(container);
  await user.click(within(dialog).getByRole("button", { name: "Done" }));
  expect(within(dialog).queryByText("hzmcp_secret-shown-once")).not.toBeInTheDocument();
  expect(await within(dialog).findByText("claude-code")).toBeInTheDocument();
  await user.click(within(dialog).getByRole("button", { name: "Revoke token claude-code" }));
  await waitFor(() => expect(within(dialog).getByText(/No tokens yet/)).toBeInTheDocument());
});

it("says why the switch or a token was refused", async () => {
  mockApi({
    ...shell,
    "/api/_web/mcp": async (request: Request) =>
      request.method === "PUT"
        ? { status: 403, body: { error: "hz.mcp_http is controlled by the environment" } }
        : { enabled: false, tokens: [] },
  });
  const user = userEvent.setup();
  renderAt("/doctor?settings=mcp");
  const dialog = await screen.findByRole("dialog", { name: "Settings" });
  await user.click(await within(dialog).findByRole("switch", { name: "Serve MCP to other agents" }));
  expect(await within(dialog).findByText("hz.mcp_http is controlled by the environment")).toBeInTheDocument();
});

it("shows under each token what its agent changed, and nothing extra for a read token that never tried", async () => {
  const reader = { ...token, id: "ee55ff66", actor: "reader", scope: "read" };
  const quiet = { ...token, id: "0011aabb", actor: "quiet" };
  mockApi({
    ...shell,
    "/api/_web/mcp": { enabled: true, tokens: [token, reader, quiet] },
    "/api/system/write/list": {
      writes: [],
      callers: [
        { caller: "token:ab12cd34", ran: 12, ran_today: 3, refused: 2, last: "2026-11-14T10:00:00Z" },
        { caller: "heinzel:s1", ran: 40, ran_today: 9, refused: 0, last: null },
      ],
      unattended_ran: 0,
    },
  });
  renderAt("/doctor?settings=mcp");
  const dialog = await screen.findByRole("dialog", { name: "Settings" });
  const row = (actor: string) => within(dialog).getByText(actor).closest("li") as HTMLElement;
  await within(dialog).findByText("Changed things 3 times in the last 24 hours, 12 in all. 2 requests did not run.");
  expect(row("claude-code").querySelector("[data-token-writes]")).not.toBeNull();
  expect(row("quiet")).toHaveTextContent("Has changed nothing.");
  expect(row("reader").querySelector("[data-token-writes]")).toBeNull();
  expect(within(dialog).queryByText(/40 in all/)).toBeNull(); // a Heinzel's line is not a token's
});

it("lists the tokens when hz keeps no record of agents' writes", async () => {
  mockApi({ ...shell, "/api/_web/mcp": { enabled: true, tokens: [token] } });
  renderAt("/doctor?settings=mcp");
  const dialog = await screen.findByRole("dialog", { name: "Settings" });
  expect(await within(dialog).findByText("claude-code")).toBeInTheDocument();
  expect(await within(dialog).findByText("Has changed nothing.")).toBeInTheDocument();
});
