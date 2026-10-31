import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { SchemaCapabilityReport, SchemaContractGraph, SchemaDiscovery, SchemaPlaybookList } from "@/api/client";
import { graph, shell } from "@/test/fixtures";
import { expectAccessible, mockApi, renderAt } from "@/test/render";
import { layoutWorkflow } from "./layout";
import { buildWorkflow, isEmpty, templateCommand } from "./model";

const emptyGraph: SchemaContractGraph = { nodes: [], edges: [], cycles: [], unmaintained: [], mermaid: "", next: [] };

const playbooks: SchemaPlaybookList = {
  next: [],
  playbooks: [
    {
      module: "teamwork",
      summary: "Draft the team's weekly report every Friday.",
      defaults: [],
      assets: [
        {
          name: "weekly-report",
          description: "The rolling document the team's reports live in.",
          kept_by: "teamwork:weekly-report",
          hints: [
            { read: "documents.read", args: {}, match: "(?i)weekly", summary: "Documents titled weekly", window: null },
          ],
        },
        { name: "team-project", description: "The tracker project the team plans in.", kept_by: null, hints: [] },
      ],
      templates: [
        {
          template: "teamwork:weekly-report",
          command: "hz heinzel new weekly-report --template teamwork:weekly-report",
          assets: ["weekly-report", "team-project"],
          keeps: ["weekly-report"],
        },
      ],
    },
  ],
};

const discovery: SchemaDiscovery = {
  playbooks: ["teamwork"],
  problems: [],
  next: [],
  candidates: [
    {
      asset: "weekly-report",
      hint: "documents.read",
      playbook: "teamwork",
      ref: "doc-123",
      title: "Payments weekly report",
      url: "https://docs.example.com/doc-123",
    },
  ],
};

const capabilities: SchemaCapabilityReport = {
  adapters: [],
  uses: [],
  capabilities: [
    { name: "tracker", bound: "github", modes: ["read"], summary: "Issues and projects." },
    { name: "documents", bound: null, modes: ["read"], summary: "Documents." },
    { name: "agent-runner", bound: "claude-code", modes: ["read"], summary: "Runs agents." },
  ],
} as unknown as SchemaCapabilityReport;

const noContracts = { directories: [], heinzelmaennchen: [], filtered: false, next: [] };

describe("the workflow model", () => {
  it("lays out sources, contracts and outputs left to right", () => {
    const { nodes, edges } = layoutWorkflow(
      buildWorkflow({ graph, contracts: shell["/api/heinzel/list"].heinzelmaennchen }),
    );
    const x = (id: string) => nodes.find((node) => node.id === id)?.position.x ?? Number.NaN;
    expect(x("cap:tracker")).toBeLessThan(x("contract:me:weekly-digest"));
    expect(x("contract:me:weekly-digest")).toBeLessThan(x("out:contract:me/weekly-digest#digest"));
    expect(edges).toHaveLength(2);
  });

  it("joins playbooks, discovery and bound systems, without the implied agent runner", () => {
    const built = buildWorkflow({ playbooks: playbooks.playbooks, discovery, capabilities: capabilities.capabilities });
    const ids = built.nodes.map((node) => node.id);
    expect(ids).toContain("cap:tracker");
    expect(ids).toContain("cap:documents");
    expect(ids).not.toContain("cap:agent-runner");
    expect(ids).toContain("asset:weekly-report/doc-123");
    expect(ids).toContain("template:teamwork:weekly-report");
    expect(built.edges.find((edge) => edge.id === "cap:documents->asset:weekly-report/doc-123")).toBeDefined();
    expect(isEmpty(built)).toBe(false);
  });

  it("fills a template's placeholders with what discovery found once", () => {
    const [template] = playbooks.playbooks[0]?.templates ?? [];
    expect(template && templateCommand(template, discovery.candidates)).toBe(
      "hz heinzel new weekly-report --template teamwork:weekly-report --asset weekly-report=doc-123",
    );
  });

  it("stops suggesting a template once a contract by its name exists", () => {
    const written = { ...shell["/api/heinzel/list"].heinzelmaennchen[0], name: "weekly-report" } as never;
    const built = buildWorkflow({ playbooks: playbooks.playbooks, contracts: [written] });
    expect(built.nodes.some((node) => node.kind === "template")).toBe(false);
  });
});

describe("the Workflow screen", () => {
  it("explains the next step when hz knows of nothing yet", async () => {
    mockApi({ ...shell, "/api/heinzel/list": noContracts, "/api/heinzel/graph": emptyGraph });
    const { container } = renderAt("/contracts");
    expect(await screen.findByText("Your workflow starts with a contract")).toBeInTheDocument();
    expect(screen.getByText("hz heinzel new weekly-digest")).toBeInTheDocument();
    expect(screen.getByText("hz system plugin search --text playbook")).toBeInTheDocument();
    await expectAccessible(container);
  });

  it("draws the templates playbooks suggest before any contract exists", async () => {
    mockApi({
      ...shell,
      "/api/heinzel/list": noContracts,
      "/api/heinzel/graph": emptyGraph,
      "/api/heinzel/playbooks": playbooks,
      "/api/system/capability/list": capabilities,
    });
    const { container } = renderAt("/contracts");
    expect(await screen.findByRole("button", { name: /^weekly-report.*, Suggested contract$/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^team-project.*, Asset type$/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^tracker.*, System$/ })).toBeInTheDocument();
    expect(screen.getByText(/1 templates your playbooks suggest/)).toBeInTheDocument();
    expect(screen.queryByText("Your workflow starts with a contract")).not.toBeInTheDocument();
    await expectAccessible(container);
  });

  it("adds discovered assets after Discover, and only then", async () => {
    const { calls } = mockApi({
      ...shell,
      "/api/heinzel/list": noContracts,
      "/api/heinzel/graph": emptyGraph,
      "/api/heinzel/playbooks": playbooks,
      "/api/heinzel/discover": discovery,
    });
    renderAt("/contracts");
    await screen.findByRole("button", { name: /^weekly-report.*, Suggested contract$/ });
    expect(calls.some((call) => call.url.includes("/heinzel/discover"))).toBe(false);
    expect(screen.queryByRole("button", { name: /Discovered asset/ })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Discover" }));
    expect(
      await screen.findByRole("button", { name: /^Payments weekly report.*, Discovered asset$/ }),
    ).toBeInTheDocument();
    expect(screen.getByText(/Discovery found 1 candidates/)).toBeInTheDocument();
  });

  it("says so when discovery fails, and keeps the graph", async () => {
    mockApi({
      ...shell,
      "/api/heinzel/list": noContracts,
      "/api/heinzel/graph": emptyGraph,
      "/api/heinzel/playbooks": playbooks,
      "/api/heinzel/discover": { status: 500, body: { error: "the tracker timed out" } },
    });
    renderAt("/contracts");
    await screen.findByRole("button", { name: /^weekly-report.*, Suggested contract$/ });
    await userEvent.click(screen.getByRole("button", { name: "Discover" }));
    // A server error is retried once, as every query is, before it shows.
    expect(await screen.findByText(/the tracker timed out/, {}, { timeout: 5000 })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^weekly-report.*, Suggested contract$/ })).toBeInTheDocument();
  });

  it("opens a node's details with its hz command", async () => {
    mockApi({
      ...shell,
      "/api/heinzel/list": noContracts,
      "/api/heinzel/graph": emptyGraph,
      "/api/heinzel/playbooks": playbooks,
    });
    const { container } = renderAt("/contracts");
    fireEvent.click(await screen.findByRole("button", { name: /^weekly-report.*, Suggested contract$/ }));
    const panel = await screen.findByRole("complementary", { name: "weekly-report" });
    expect(
      within(panel).getByText("hz heinzel new weekly-report --template teamwork:weekly-report"),
    ).toBeInTheDocument();
    expect(
      within(panel).getByRole("button", { name: /Run in terminal: hz heinzel new weekly-report/ }),
    ).toBeInTheDocument();
    await expectAccessible(container);
    await userEvent.click(within(panel).getByRole("button", { name: "Close details" }));
    await waitFor(() => expect(screen.queryByRole("complementary", { name: "weekly-report" })).toBeNull());
  });

  it("shows existing contracts as solid nodes that open the contract", async () => {
    mockApi({ ...shell, "/api/heinzel/graph": graph });
    renderAt("/contracts");
    fireEvent.click(await screen.findByRole("button", { name: /^Weekly digest.*, Contract$/ }));
    const panel = await screen.findByRole("complementary", { name: "Weekly digest" });
    expect(within(panel).getByText("hz heinzel show me:weekly-digest")).toBeInTheDocument();
    expect(within(panel).getByRole("link", { name: "Open the contract" })).toHaveAttribute(
      "href",
      "/contracts/me%3Aweekly-digest",
    );
  });
});
