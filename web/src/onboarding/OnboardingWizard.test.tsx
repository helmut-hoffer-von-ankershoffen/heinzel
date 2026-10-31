import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { connectTerminal, runInTerminal } from "@/lib/terminal";
import { config, doctor, noTeam, profile, shell } from "@/test/fixtures";
import { expectAccessible, mockApi, renderAt } from "@/test/render";
import { contractName, loadProgress, setupIncomplete } from "./progress";

const answers = {
  ...shell,
  "/api/system/doctor": doctor,
  "/api/team/show": noTeam,
  "/api/system/daemon/jobs": [],
  "/api/system/layer/list": {
    layers: [
      {
        name: "acme-payments",
        label: "team",
        module: "acme_payments",
        parent: "acme",
        hint: null,
        adapters: ["jira:tracker"],
        playbook: "Weekly status for payments",
      },
    ],
    next: [],
    problems: [],
  },
  "/api/system/config/set": { section: "org", key: "role", value: "engineer", scope: "user", path: "/tmp/x" },
};

const newcomer = { ...profile, role: null, timezone: null };

afterEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});

describe("progress", () => {
  it("asks while role or time zone is unknown, and names a contract from a sentence", () => {
    expect(setupIncomplete(undefined)).toBe(false);
    expect(setupIncomplete(profile)).toBe(false);
    expect(setupIncomplete(newcomer)).toBe(true);
    expect(contractName("Send the Wöchentliche status, to my team!")).toBe("send-the-wochentliche-status");
    localStorage.setItem("hz.onboarding", "{not json");
    expect(loadProgress()).toEqual({ step: "you", visited: [], closed: false });
    localStorage.setItem("hz.onboarding", JSON.stringify({ step: "nope", visited: ["look", "x"], closed: true }));
    expect(loadProgress()).toEqual({ step: "you", visited: ["look"], closed: true });
  });
});

describe("Onboarding", { timeout: 20_000 }, () => {
  it("stays closed once hz knows you, and opens from the account menu", async () => {
    mockApi(answers);
    const user = userEvent.setup();
    renderAt("/doctor");
    await user.click(await screen.findByRole("button", { name: /mira/i }, { timeout: 5000 }));
    expect(screen.queryByRole("dialog", { name: "Set up hz" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("menuitem", { name: "Set up hz" }));
    expect(await screen.findByRole("dialog", { name: "Set up hz" })).toBeInTheDocument();
  });

  it("opens on a first run, saves answers through hz, remembers the step and closes with Escape", async () => {
    const { calls } = mockApi({ ...answers, "/api/org/show": newcomer });
    const user = userEvent.setup();
    const { container } = renderAt("/doctor");
    const dialog = await screen.findByRole("dialog", { name: "Set up hz" });
    expect(within(dialog).getByRole("heading", { name: "Who you are" })).toHaveFocus();
    await expectAccessible(container);
    await user.selectOptions(within(dialog).getByLabelText("Role"), "engineer");
    await waitFor(() =>
      expect(calls.some((call) => call.url.includes("/api/system/config/set") && call.url.includes("org.role"))).toBe(
        true,
      ),
    );
    await user.click(within(dialog).getByRole("button", { name: "Skip" }));
    expect(within(dialog).getByRole("heading", { name: "Your team" })).toHaveFocus();
    expect(within(dialog).getByRole("button", { name: "Join this team" })).toBeDisabled();
    await user.type(within(dialog).getByLabelText("Team repository"), "https://github.com/acme/payments-team");
    await user.click(within(dialog).getByRole("button", { name: "Join this team" }));
    expect(within(dialog).getByText("hz team join https://github.com/acme/payments-team")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await user.selectOptions(await within(dialog).findByLabelText("Team or org layer"), "acme-payments");
    await waitFor(() => expect(calls.some((call) => call.url.includes("team.layer"))).toBe(true));
    expect(JSON.parse(localStorage.getItem("hz.onboarding") ?? "{}")).toMatchObject({ step: "team" });
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(JSON.parse(localStorage.getItem("hz.onboarding") ?? "{}")).toMatchObject({ step: "team", closed: true });
  });

  it("walks every step to the end, turning a sentence into hz heinzel new", async () => {
    mockApi({ ...answers, "/api/org/show": newcomer });
    const user = userEvent.setup();
    renderAt("/doctor");
    const dialog = await screen.findByRole("dialog", { name: "Set up hz" });
    const steps = within(dialog).getByRole("navigation", { name: "Setup steps" });
    await user.click(within(steps).getByRole("button", { name: /Your first Heinzelmännchen/ }));
    expect(within(dialog).getByText("Step 7 of 7")).toBeInTheDocument();
    await user.type(within(dialog).getByLabelText("Or in one sentence"), "Weekly status for Acme");
    expect(within(dialog).getByText("Its contract will be called weekly-status-for-acme.")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Create it" }));
    expect(within(dialog).getByText("hz heinzel new weekly-status-for-acme")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Back" }));
    expect(within(dialog).getByRole("heading", { name: "Notifications" })).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Install the daemon" })).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Next" }));
    await user.click(within(dialog).getByRole("button", { name: "Finish" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(JSON.parse(localStorage.getItem("hz.onboarding") ?? "{}")).toMatchObject({ closed: true });
  });
});

describe("runInTerminal", () => {
  it("turns control characters into spaces, so a line break can't run half a command", () => {
    const sent: string[] = [];
    connectTerminal((text) => sent.push(text));
    runInTerminal("hz team join https://github.com/acme/team");
    runInTerminal("hz heinzel new x\nrm -rf ~");
    connectTerminal(null);
    expect(sent).toEqual(["hz team join https://github.com/acme/team\r", "hz heinzel new x rm -rf ~"]);
  });
});

describe("Onboarding focus and answers", { timeout: 20_000 }, () => {
  it("stays open once the first answers complete setup, and keeps Tab inside", async () => {
    let known = false;
    mockApi({ ...answers, "/api/org/show": newcomer });
    const original = vi.mocked(globalThis.fetch).getMockImplementation();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (request: Request) => {
        const path = new URL(request.url).pathname;
        if (path === "/api/system/config/set") known = true;
        if (path === "/api/org/show")
          return Response.json(known ? { ...newcomer, role: "engineer", timezone: "UTC" } : newcomer);
        return original?.(request);
      }),
    );
    const user = userEvent.setup();
    renderAt("/doctor");
    const dialog = await screen.findByRole("dialog", { name: "Set up hz" });
    await user.selectOptions(await within(dialog).findByLabelText("Role"), "engineer");
    await waitFor(() => expect(within(dialog).getByLabelText("Role")).toHaveValue("engineer"));
    expect(screen.getByRole("dialog", { name: "Set up hz" })).toBeInTheDocument();
    within(dialog).getByRole("heading", { name: "Who you are" }).focus();
    await user.tab({ shift: true });
    expect(dialog).toContainElement(document.activeElement as HTMLElement);
    within(dialog).getByRole("button", { name: "Next" }).focus();
    await user.tab();
    expect(dialog).toContainElement(document.activeElement as HTMLElement);
  });

  it("offers exactly four roles, in this order, labeled as the owner named them, and no others", async () => {
    mockApi({ ...answers, "/api/org/show": newcomer });
    renderAt("/doctor");
    const dialog = await screen.findByRole("dialog", { name: "Set up hz" });
    const role = within(dialog).getByLabelText("Role");
    expect(
      within(role)
        .getAllByRole("option")
        .map((option) => option.textContent),
    ).toEqual([
      "not set",
      "IC - Engineer",
      "EM - Engineering Manager",
      "PM - Product Manager",
      "TPM - Program Manager",
    ]);
  });

  it("shows a saved role that is no longer offered as not set, with a prompt to choose one", async () => {
    mockApi({ ...answers, "/api/org/show": { ...newcomer, retired_role: "principal" } });
    renderAt("/doctor");
    const dialog = await screen.findByRole("dialog", { name: "Set up hz" });
    expect(within(dialog).getByLabelText("Role")).toHaveValue("");
    expect(await within(dialog).findByText(/Your saved role "principal" is no longer offered/)).toBeInTheDocument();
  });

  it("opens Settings in place of the wizard, and Escape in a question cancels only the question", async () => {
    mockApi({ ...answers, "/api/org/show": newcomer });
    const user = userEvent.setup();
    renderAt("/doctor");
    const dialog = await screen.findByRole("dialog", { name: "Set up hz" });
    await user.click(within(dialog).getByRole("button", { name: /Notifications/ }));
    await user.click(within(dialog).getByRole("button", { name: "Install the daemon" }));
    expect(within(dialog).getByRole("button", { name: "Run it" })).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(screen.getByRole("dialog", { name: "Set up hz" })).toBeInTheDocument();
    await waitFor(() => expect(within(dialog).getByRole("button", { name: "Install the daemon" })).toHaveFocus());
    await user.click(within(dialog).getByRole("button", { name: /AI access/ }));
    await user.click(within(dialog).getByRole("link", { name: "Open AI settings" }));
    expect(screen.queryByRole("dialog", { name: "Set up hz" })).not.toBeInTheDocument();
  });
});

describe("Onboarding team name", { timeout: 20_000 }, () => {
  const named = (name: string) => ({
    ...config,
    settings: [
      ...config.settings,
      {
        section: "team",
        key: "name",
        value: name,
        default: null,
        source: "user",
        env: "HZ_TEAM__NAME",
        description: "Team name.",
      },
    ],
  });

  it("saves the team name through hz when the field is left, and shows it again after a reload", async () => {
    const { calls } = mockApi({ ...answers, "/api/org/show": newcomer });
    const user = userEvent.setup();
    const first = renderAt("/doctor");
    const dialog = await screen.findByRole("dialog", { name: "Set up hz" });
    await user.click(within(dialog).getByRole("button", { name: /Your team/ }));
    await user.type(await within(dialog).findByLabelText("Team name"), "Payments{Enter}");
    await waitFor(() => {
      const set = calls.find((call) => call.url.includes("/api/system/config/set") && call.url.includes("team.name"));
      expect(new URL(set?.url ?? "http://x").searchParams.get("value")).toBe("Payments");
    });
    first.unmount();
    // A reload: hz now reports the saved name.
    mockApi({ ...answers, "/api/org/show": newcomer, "/api/system/config/show": named("Payments") });
    renderAt("/doctor");
    const again = await screen.findByRole("dialog", { name: "Set up hz" });
    await userEvent.setup().click(within(again).getByRole("button", { name: /Your team/ }));
    expect(await within(again).findByLabelText("Team name")).toHaveValue("Payments");
  });
});

describe("Onboarding notifications step", { timeout: 20_000 }, () => {
  const channel = (name: string, state: string, extra = {}) => ({
    name,
    state,
    detail: `${name} detail`,
    fix: null,
    command: null,
    ...extra,
  });
  const blocked = {
    notice: { id: "n1", title: "Test", body: "x", severity: "urgent" },
    native: "macOS doesn't allow Heinzel Notifier",
    pops_up: false,
    channels: [
      channel("web", "ready"),
      channel("native", "blocked", { fix: "open-settings" }),
      channel("daemon", "missing", { fix: "install-daemon", command: "hz system daemon install" }),
    ],
  };
  const idle = { installed: false, loaded: false, paused: false, jobs: 0, failing: [] };
  const open = async (extra: Record<string, unknown>) => {
    mockApi({
      ...answers,
      "/api/org/show": newcomer,
      "/api/system/notify/list": { notices: [], waiting: 0 },
      ...extra,
    });
    const user = userEvent.setup();
    renderAt("/doctor");
    const dialog = await screen.findByRole("dialog", { name: "Set up hz" });
    await user.click(within(dialog).getByRole("button", { name: /Notifications/ }));
    return { user, dialog };
  };

  it("guides iPhone tabs to the Home Screen without offering server installation or an ineffective permission button", async () => {
    vi.spyOn(navigator, "userAgent", "get").mockReturnValue("Mozilla/5.0 (iPhone) AppleWebKit/605.1.15");
    const { user, dialog } = await open({ "/api/system/notify/test": blocked });
    expect(within(dialog).getByRole("button", { name: "Allow notifications" })).toBeDisabled();
    expect(within(dialog).getByText(/Add to Home Screen/)).toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: "Install the daemon" })).not.toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Send a test notice" }));
    const result = await within(dialog).findByRole("region", { name: "How the test went" });
    expect(result).not.toHaveTextContent(/macOS|native detail/);
  });

  it("shows how each way of reaching you stands after the test notice, with its fix", async () => {
    const { user, dialog } = await open({
      "/api/system/notify/test": { status: 503, body: blocked },
      "/api/system/daemon/status": idle,
    });
    await user.click(within(dialog).getByRole("button", { name: "Send a test notice" }));
    const result = await within(dialog).findByRole("region", { name: "How the test went" });
    expect(within(result).getByText(/native detail/)).toBeInTheDocument();
    expect(within(result).getByRole("button", { name: "Install daemon" })).toBeInTheDocument();
  });

  it("says why the test notice failed", async () => {
    const { user, dialog } = await open({
      "/api/system/notify/test": { status: 400, body: { error: "Notifications are off" } },
      "/api/system/daemon/status": idle,
    });
    await user.click(within(dialog).getByRole("button", { name: "Send a test notice" }));
    expect(await within(dialog).findByText(/Notifications are off/)).toHaveAttribute("role", "alert");
  });

  it("closes the dialog when the daemon install goes to the terminal, so the terminal is seen", async () => {
    const sent: string[] = [];
    connectTerminal((text) => sent.push(text));
    const { user, dialog } = await open({ "/api/system/daemon/status": idle });
    await user.click(within(dialog).getByRole("button", { name: "Install the daemon" }));
    await user.click(within(dialog).getByRole("button", { name: "Run it" }));
    expect(sent).toEqual(["hz system daemon install\r"]);
    expect(screen.queryByRole("dialog", { name: "Set up hz" })).not.toBeInTheDocument();
    connectTerminal(null);
  });

  it("says the daemon is installed and running, or installed but not running", async () => {
    const running = await open({
      "/api/system/daemon/status": { ...idle, installed: true, loaded: true },
    });
    expect(await within(running.dialog).findByText("Daemon installed and running")).toBeInTheDocument();
    expect(within(running.dialog).queryByRole("button", { name: "Install the daemon" })).not.toBeInTheDocument();
  });

  it("says when the daemon is installed but not running", async () => {
    const { dialog } = await open({ "/api/system/daemon/status": { ...idle, installed: true } });
    expect(await within(dialog).findByText(/Installed, but not running yet/)).toBeInTheDocument();
  });
});
