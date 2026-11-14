import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it } from "vitest";
import { shell } from "@/test/fixtures";
import { expectAccessible, mockApi, renderAt } from "@/test/render";

const token = {
  id: "ab12cd34",
  actor: "claude-code",
  scope: "write",
  created: "2026-11-14T10:00:00Z",
  expires: "2027-02-12T10:00:00Z",
  last_used: null,
};

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
