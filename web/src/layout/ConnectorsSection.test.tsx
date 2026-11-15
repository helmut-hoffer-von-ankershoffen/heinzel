import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ConnectorsSection } from "./ConnectorsSection";

vi.mock("@/api/queries", () => ({
  useCapabilities: () => ({ data: { capabilities: [], adapters: [] } }),
  useConnectorReport: () => ({ data: undefined, isPending: false }),
  usePluginsPending: () => ({ data: [] }),
}));
vi.mock("./PluginsPending", () => ({ PluginsPending: () => null }));

const props = { open: undefined, settings: [], onOpen: vi.fn(), renderSettings: () => null };

describe("Connectors while the installed plugins load", () => {
  it("says it is looking, never that none is installed", () => {
    render(<ConnectorsSection connectors={[]} loading {...props} />);
    expect(screen.getByRole("status")).toHaveTextContent("Looking for installed connectors…");
    expect(screen.queryByText(/No connector installed yet/)).not.toBeInTheDocument();
  });

  it("says none is installed once the plugins are known", () => {
    render(<ConnectorsSection connectors={[]} {...props} />);
    expect(screen.getByText(/No connector installed yet/)).toBeInTheDocument();
  });
});
