import { render, screen, within } from "@testing-library/react";
import { expect, it } from "vitest";
import type { SchemaContractVersion } from "@/api/client";
import { ContractHistory } from "@/chat/ContractHistory";

it("shows persisted proposed and approved versions without implying a decision", () => {
  const base: SchemaContractVersion = {
    id: "contract-one",
    version: 1,
    sha256: "a".repeat(64),
    status: "approved",
    title: "Keep the artifact current",
    text: "Use the agreed sources.",
    created_at: "2026-10-29T10:00:00Z",
  };
  render(
    <ContractHistory
      contracts={[
        base,
        { ...base, version: 2, sha256: "b".repeat(64), status: "proposed", parent_version: 1, text: "New terms." },
      ]}
    />,
  );
  const panel = within(screen.getByRole("region", { name: "Agent contract" }));
  expect(panel.getByText("Contract v1: approved")).toBeInTheDocument();
  expect(panel.getByText("Contract v2: proposed · amends version 1")).toBeInTheDocument();
  expect(panel.getByText("New terms.")).toBeInTheDocument();
  expect(panel.queryByRole("button")).not.toBeInTheDocument();
});
