import { screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { shell } from "@/test/fixtures";
import { mockApi, renderAt } from "@/test/render";

const base = {
  name: "weekly-digest",
  namespace: "me",
  title: "Weekly digest",
  description: "Summarises the week.",
  file: "/home/mira/workflows/weekly-digest.prose.md",
  schedule: "weekly",
  ready: true,
  draft_count: 2,
  why_not_scheduled: null,
  requirements: [{ requirement: "tracker:read", adapter: "acme", problem: null, fix: null }],
  inputs: { issues: "tracker.issues" },
  outputs: { digest: "document" },
  diagnostics: [],
  next: [{ command: "hz heinzel run me:weekly-digest", why: "Run it now" }],
};

const show = (contract: object) => {
  const { calls } = mockApi({ ...shell, "/api/heinzel/show": contract });
  return calls;
};

describe("The contract screen", () => {
  beforeEach(() => sessionStorage.setItem("hz.token", "test-token"));

  it("asks for the contract named in the address, decoding it", async () => {
    const calls = show(base);
    renderAt("/contracts/me%3Aweekly-digest");
    await screen.findByRole("heading", { name: "Weekly digest" });
    const read = calls.find((call) => call.url.includes("/heinzel/show"));
    expect(new URL(read?.url ?? "").searchParams.get("name")).toBe("me:weekly-digest");
  });

  it("shows a ready contract with its schedule, drafts, file and next steps", async () => {
    show(base);
    renderAt("/contracts/me%3Aweekly-digest");
    const main = within(await screen.findByRole("main"));
    expect(await main.findByText("ready")).toBeInTheDocument();
    expect(main.getByText("Schedule: weekly")).toBeInTheDocument();
    expect(main.getByText("2 drafts so far")).toBeInTheDocument();
    expect(main.getByText(base.file)).toBeInTheDocument();
    expect(main.getByText("hz heinzel run me:weekly-digest")).toBeInTheDocument();
    expect(main.getByText("Run it now")).toBeInTheDocument();
  });

  it("says a contract with no schedule runs by hand and why it is not scheduled", async () => {
    show({ ...base, schedule: null, ready: false, why_not_scheduled: "Needs a tracker first." });
    renderAt("/contracts/me%3Aweekly-digest");
    const main = within(await screen.findByRole("main"));
    expect(await main.findByText("not ready")).toBeInTheDocument();
    expect(main.getByText("Schedule: by hand only")).toBeInTheDocument();
    expect(main.getByText("Needs a tracker first.")).toBeInTheDocument();
  });

  it("shows an unmet requirement with its problem and fix, and a met one with its adapter", async () => {
    show({
      ...base,
      requirements: [
        { requirement: "tracker:read", adapter: "acme", problem: null, fix: null },
        { requirement: "mail:write", adapter: null, problem: "No mail connected", fix: "hz plugin add mail" },
      ],
    });
    renderAt("/contracts/me%3Aweekly-digest");
    const main = within(await screen.findByRole("main"));
    expect(await main.findByText("acme")).toBeInTheDocument();
    expect(main.getByText("✖")).toBeInTheDocument();
    expect(main.getByText("No mail connected")).toBeInTheDocument();
    expect(main.getByText("hz plugin add mail")).toBeInTheDocument();
  });

  it("lists problems found in the contract only when there are some", async () => {
    show({
      ...base,
      diagnostics: [
        { code: "E101", severity: "error", line: 7, field: "inputs", message: "Unknown input", hint: "Check the name" },
      ],
    });
    renderAt("/contracts/me%3Aweekly-digest");
    const main = within(await screen.findByRole("main"));
    expect(await main.findByText("E101")).toBeInTheDocument();
    expect(main.getByText(/line\s*7: Unknown input Check the name/)).toBeInTheDocument();
  });

  it("has no problems card for a clean contract", async () => {
    show(base);
    renderAt("/contracts/me%3Aweekly-digest");
    const main = within(await screen.findByRole("main"));
    await main.findByText("ready");
    expect(main.queryByText("Problems")).not.toBeInTheDocument();
  });
});
