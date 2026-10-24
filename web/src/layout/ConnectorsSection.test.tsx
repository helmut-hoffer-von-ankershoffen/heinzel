import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { SchemaPluginInfo } from "@/api/client";
import * as terminal from "@/lib/terminal";
import { choice, connector, connectorReport, shell } from "@/test/fixtures";
import { expectAccessible, mockApi, renderAt } from "@/test/render";
import { pill } from "./ConnectorsSection";

const github = {
  name: "github",
  distribution: "heinzel-github",
  version: "0.5.0",
  modules: ["github"],
  summary: "Read pull requests and issues from GitHub.",
  source_kind: "git",
  source_url: null,
  revision: null,
  homepage: null,
  first_party: true,
  enabled: true,
  error: null,
  layers: [],
  capabilities: ["github:code-host", "github:tracker"],
  playbooks: [],
  themes: [],
  kinds: ["connector"],
} as unknown as SchemaPluginInfo;

const answers = (report = connectorReport(), extra: Record<string, unknown> = {}) => ({
  ...shell,
  "/api/system/plugin/list": [github],
  "/api/system/plugin/search": { plugins: [], next: [] },
  "/api/system/connector/list": report,
  "/api/system/config/set": "/home/mira/.config/hz/hz.toml",
  "/api/system/config/unset": "/home/mira/.config/hz/hz.toml",
  ...extra,
});

const open = async () => {
  renderAt("/doctor?settings=connectors");
  return screen.findByRole("dialog", { name: "Settings" });
};

const query = (calls: Request[], path: string) =>
  calls.filter((call) => call.url.includes(path)).map((call) => Object.fromEntries(new URL(call.url).searchParams));

describe("the connector pill", () => {
  it.each([
    [connector(), undefined, "Connected as ada"],
    [connector({ login: null }), undefined, "Connected"],
    [connector({ state: "not-signed-in" }), undefined, "Not signed in"],
    [connector({ state: "not-bound" }), undefined, "Not bound"],
    [connector({ state: "attention" }), undefined, "Needs attention"],
    [connector({ state: "unknown", login: null }), undefined, "Not tested"],
    [connector(), { name: "github", passed: false, latency_ms: 3, login: null, checks: [] }, "Test failed"],
    [
      connector({ state: "unknown", login: null }),
      { name: "github", passed: true, latency_ms: 3, login: { signed_in: true, account: "bo" }, checks: [] },
      "Connected as bo",
    ],
  ] as const)("says %#", (given, tested, label) => {
    expect(pill(given, tested).label).toBe(label);
  });
});

describe("Settings → Connectors", () => {
  it("shows each card's state, its tools on demand, and is accessible", async () => {
    mockApi(answers());
    const user = userEvent.setup();
    const dialog = await open();
    const card = await within(dialog).findByRole("article", { name: "GitHub" });
    expect(within(card).getByText("Connected as ada")).toBeInTheDocument();
    expect(within(card).queryByText("code-host.merged_pull_requests")).not.toBeInTheDocument();
    await user.click(within(card).getByRole("button", { name: /What hz can do through it \(2\)/ }));
    expect(within(card).getByText("code-host.merged_pull_requests")).toBeInTheDocument();
    expect(within(card).getAllByText("read")).toHaveLength(2);
    await expectAccessible(dialog);
  });

  it("tests a connector only when asked, and shows the result and latency", async () => {
    const tested = { name: "github", passed: true, latency_ms: 42, login: { signed_in: true, account: "ada" }, checks: [] };
    const { calls } = mockApi(answers(connectorReport(), { "/api/system/connector/test": tested }));
    const user = userEvent.setup();
    const dialog = await open();
    const card = await within(dialog).findByRole("article", { name: "GitHub" });
    expect(query(calls, "/system/connector/test")).toEqual([]);
    await user.click(within(card).getByRole("button", { name: "Test" }));
    expect(await within(card).findByText("Works: answered in 42 ms")).toBeInTheDocument();
    expect(query(calls, "/system/connector/test")).toEqual([{ name: "github" }]);
  });

  it("a failed test says why and offers Connect, which types the sign-in command into the terminal", async () => {
    const failed = {
      name: "github",
      passed: false,
      latency_ms: 7,
      login: { signed_in: false },
      checks: [{ name: "whoami", status: "degraded", detail: "gh is not logged in", fix: "gh auth login" }],
    };
    mockApi(answers(connectorReport(), { "/api/system/connector/test": failed }));
    const run = vi.spyOn(terminal, "runInTerminal").mockImplementation(() => {});
    const user = userEvent.setup();
    const dialog = await open();
    const card = await within(dialog).findByRole("article", { name: "GitHub" });
    await user.click(within(card).getByRole("button", { name: "Test" }));
    expect(await within(card).findByText("Test failed")).toBeInTheDocument();
    expect(within(card).getByText("Failed after 7 ms: gh is not logged in")).toBeInTheDocument();
    await user.click(within(card).getByRole("button", { name: "Connect" }));
    expect(run).toHaveBeenCalledWith("gh auth login --hostname github.com");
    run.mockRestore();
  });

  it("binds an unused capability from its card only after asking", async () => {
    const { calls } = mockApi(answers());
    const user = userEvent.setup();
    const dialog = await open();
    const card = await within(dialog).findByRole("article", { name: "GitHub" });
    await user.click(within(card).getByRole("button", { name: "Use for Tracker" }));
    expect(query(calls, "/system/config/set")).toEqual([]);
    expect(within(card).getByText("Use GitHub for Tracker?")).toBeInTheDocument();
    await user.click(within(card).getByRole("button", { name: "Use it" }));
    await waitFor(() =>
      expect(query(calls, "/system/config/set")).toEqual([
        { key: "capabilities.tracker", value: "github", scope: "user" },
      ]),
    );
  });

  it("lists capabilities in plain words with a picker, the automatic choice shown, and saves at once with undo", async () => {
    const { calls } = mockApi(answers());
    const user = userEvent.setup();
    const dialog = await open();
    const rows = await within(dialog).findByRole("list", { name: "Which connector serves what" });
    expect(within(rows).getByText("Code host")).toBeInTheDocument();
    expect(within(rows).getByText(/pull requests and reviews/)).toBeInTheDocument();
    const codeHost = within(rows).getByLabelText(/Code host/) as HTMLSelectElement;
    expect(codeHost.selectedOptions[0]?.textContent).toBe("GitHub (automatic)");
    expect(within(rows).queryByRole("textbox")).not.toBeInTheDocument();
    const tracker = within(rows).getByLabelText(/Tracker/) as HTMLSelectElement;
    expect([...tracker.options].map((option) => option.textContent)).toEqual([
      "Choose…",
      "GitHub · connected",
      "Atlassian · not signed in",
      "None",
    ]);
    await user.selectOptions(tracker, "github");
    await waitFor(() =>
      expect(query(calls, "/system/config/set")).toEqual([
        { key: "capabilities.tracker", value: "github", scope: "user" },
      ]),
    );
    const status = await within(rows).findByText("Tracker now uses GitHub.");
    await user.click(within(status.closest("p") as HTMLElement).getByRole("button", { name: "Undo" }));
    await waitFor(() =>
      expect(query(calls, "/system/config/unset")).toEqual([{ key: "capabilities.tracker", scope: "user" }]),
    );
  });

  it("says when nothing serves a capability and finds one under Plugins → Discover", async () => {
    const { calls } = mockApi(answers());
    const user = userEvent.setup();
    const dialog = await open();
    const rows = await within(dialog).findByRole("list", { name: "Which connector serves what" });
    expect(within(rows).getByText("No connector installed")).toBeInTheDocument();
    await user.click(within(rows).getByRole("button", { name: "Find one" }));
    expect(await within(dialog).findByRole("tab", { name: "Discover", selected: true })).toBeInTheDocument();
    expect(within(dialog).getByRole("searchbox", { name: "Search plugins" })).toHaveValue("chat");
    await waitFor(() => expect(query(calls, "/system/plugin/search").at(-1)).toMatchObject({ text: "chat" }));
  });

  it("offers Connect beside a chosen connector that isn't signed in", async () => {
    const atlassian = connector({ name: "atlassian", title: "Atlassian", state: "not-signed-in", connect: ["acli jira auth login"] });
    const report = connectorReport(
      [connector(), atlassian],
      [
        choice({
          capability: "tracker",
          title: "Tracker",
          key: "capabilities.tracker",
          bound: "atlassian",
          how: "configured",
          candidates: [{ adapter: "atlassian", connector: "atlassian", title: "Atlassian", state: "not-signed-in" }],
        }),
      ],
    );
    report.connectors[1].capabilities = [
      { capability: "tracker", title: "Tracker", adapter: "atlassian", modes: ["read"], used: true, bound: "atlassian", how: "configured" },
    ];
    mockApi(answers(report));
    const run = vi.spyOn(terminal, "runInTerminal").mockImplementation(() => {});
    const user = userEvent.setup();
    const dialog = await open();
    const rows = await within(dialog).findByRole("list", { name: "Which connector serves what" });
    await user.click(within(rows).getByRole("button", { name: "Connect" }));
    expect(run).toHaveBeenCalledWith("acli jira auth login");
    run.mockRestore();
  });
});
