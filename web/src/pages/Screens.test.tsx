import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { config, noTeam, profile, shell } from "@/test/fixtures";
import { expectAccessible, mockApi } from "@/test/render";
import { renderAt } from "@/test/renderApp";

vi.mock("@/features/runtime/lifecycle/RestartAndApply", () => ({
  RestartAndApply: ({ onUnsupported }: { onUnsupported: () => Promise<void> }) => (
    <button type="button" onClick={onUnsupported}>
      Save staged setting
    </button>
  ),
}));

describe("Organisation", () => {
  it("says why there is no org chart yet and what to look at", async () => {
    mockApi(shell);
    const { container } = renderAt("/org");
    expect(await screen.findByText(/Reporting lines are guessed from your calendar/)).toBeInTheDocument();
    await expectAccessible(container);
  });

  it("shows how to join or start a team when there is none", async () => {
    mockApi({ ...shell, "/api/team/show": noTeam });
    renderAt("/team");
    expect(await screen.findByText("hz team join <url>")).toBeInTheDocument();
    expect(screen.getByText("hz team new <name>")).toBeInTheDocument();
  });
});

describe("Profile", () => {
  it("lists who you are", async () => {
    mockApi(shell);
    const { container } = renderAt("/profile");
    expect(await screen.findByText("engineering-manager")).toBeInTheDocument();
    expect(screen.getByText("Europe/Berlin")).toBeInTheDocument();
    await expectAccessible(container);
  });

  it("shows the team name the person gave, and asks for one when there is none", async () => {
    mockApi({
      ...shell,
      "/api/system/config/show": {
        ...config,
        settings: [
          ...config.settings,
          {
            section: "team",
            key: "name",
            value: "Payments",
            default: null,
            source: "user",
            env: "HZ_TEAM__NAME",
            description: "Team name.",
          },
        ],
      },
    });
    renderAt("/profile");
    expect(await screen.findByText("Payments")).toBeInTheDocument();
  });

  it("links an unset team name to its setting", async () => {
    mockApi(shell);
    renderAt("/profile");
    await screen.findByText("engineering-manager");
    expect(screen.getByText("Team").closest("div")).toHaveTextContent("not set");
    // A value that is missing is a button into its setting, not a text link.
    expect(within(screen.getByText("Team").closest("div") as HTMLElement).getByRole("button")).toHaveTextContent(
      "not set",
    );
  });

  it.each(["mira", null])("edits handle %s and reads it again after remount", async (initial) => {
    let handle = initial;
    const { calls } = mockApi({
      ...shell,
      "/api/org/show": () => ({ ...profile, handle }),
      "/api/system/config/show": () => ({
        ...config,
        settings: [
          ...config.settings,
          {
            section: "org",
            key: "handle",
            value: handle,
            default: null,
            source: "user",
            env: "HZ_ORG__HANDLE",
            description: "Your handle.",
            json_schema: { type: "string", title: "Handle" },
          },
        ],
      }),
      "/api/system/config/set": (request: Request) => {
        handle = new URL(request.url).searchParams.get("value");
        return "config.toml";
      },
    });
    const user = userEvent.setup();
    const view = renderAt("/profile");
    expect(await screen.findByRole("button", { name: "Edit profile" })).toBeInTheDocument();
    expect(screen.queryByText("Run in terminal")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: `Edit Handle, currently ${initial || "not set"}` }));
    const settings = await screen.findByRole("dialog", { name: "Settings" });
    const input = within(settings).getByRole("textbox", { name: "Handle" });
    await user.clear(input);
    await user.type(input, "mira-two{Enter}");
    expect(calls.some((call) => call.url.includes("key=org.handle&value=mira-two"))).toBe(false);
    await user.click(screen.getByRole("button", { name: "Save staged setting" }));
    await waitFor(() => expect(calls.some((call) => call.url.includes("key=org.handle&value=mira-two"))).toBe(true));
    await user.keyboard("{Escape}");
    expect(await screen.findByRole("button", { name: "Edit Handle, currently mira-two" })).toBeInTheDocument();
    view.unmount();
    const reads = calls.filter((call) => new URL(call.url).pathname === "/api/org/show").length;
    renderAt("/profile");
    expect(await screen.findByRole("button", { name: "Edit Handle, currently mira-two" })).toBeInTheDocument();
    expect(calls.filter((call) => new URL(call.url).pathname === "/api/org/show").length).toBeGreaterThan(reads);
  });
});
