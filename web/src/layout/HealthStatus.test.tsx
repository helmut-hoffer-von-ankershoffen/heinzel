import { act, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { Providers, queryClient } from "@/App";
import { doctor } from "@/test/fixtures";
import { expectAccessible, mockApi } from "@/test/render";
import { HealthStatus } from "./HealthStatus";

function show(report: unknown = doctor) {
  mockApi({ "/api/system/doctor": report });
  const client = queryClient();
  return {
    client,
    ...render(
      <Providers client={client}>
        <MemoryRouter>
          <HealthStatus />
        </MemoryRouter>
      </Providers>,
    ),
  };
}
describe("footer system health", () => {
  it("shows actionable degraded checks and a keyboard-accessible Doctor link", async () => {
    const { container } = show();
    const trigger = await screen.findByRole("button", { name: "System health, warning" });
    fireEvent.focus(trigger);
    expect(screen.getByRole("region")).toHaveTextContent("1 healthy · 2 need attention · 0 unavailable");
    expect(screen.getByRole("region")).toHaveTextContent("missing org.handle");
    expect(screen.getByRole("region")).toHaveTextContent("Run `hz setup`");
    expect(screen.getByRole("link", { name: "Open Doctor" })).toHaveAttribute("href", "/doctor");
    await expectAccessible(container);
  });
  it("keeps a typed DOWN result returned as HTTP 503", async () => {
    show({ status: 503, body: { ...doctor, status: "down" } });
    const trigger = await screen.findByRole("button", { name: "System health, error" });
    expect(trigger.closest("[data-state]")).toHaveAttribute("data-state", "bad");
  });
  it("keeps empty and failed reports unknown instead of green", async () => {
    const { client } = show({ status: "up", checks: {} });
    const trigger = await screen.findByRole("button", { name: "System health, off" });
    fireEvent.click(trigger);
    expect(await screen.findByText("No health checks are available.")).toBeInTheDocument();
    mockApi({ "/api/system/doctor": { status: 500, body: { error: "unavailable" } } });
    await act(() => client.refetchQueries({ queryKey: ["system", "doctor"] }));
    expect(await screen.findByRole("status")).toHaveTextContent("could not be refreshed");
    expect(trigger.closest("[data-state]")).toHaveAttribute("data-state", "off");
  });
  it("hides an old healthy sample after a failed refresh", async () => {
    const { client } = show({
      status: "up",
      checks: { demo: [{ name: "network", status: "up", detail: "Available", fix: "" }] },
    });
    fireEvent.click(await screen.findByRole("button", { name: "System health, OK" }));
    mockApi({ "/api/system/doctor": { status: 500, body: { error: "unavailable" } } });
    await act(() => client.refetchQueries({ queryKey: ["system", "doctor"] }));
    expect(await screen.findByRole("status")).toHaveTextContent("could not be refreshed");
    expect(screen.getByRole("button", { name: "System health, off" })).toBeInTheDocument();
    expect(screen.queryByText("1 healthy · 0 need attention · 0 unavailable")).not.toBeInTheDocument();
  });
  it("puts unavailable checks before warnings in the short pane", async () => {
    show({
      status: "down",
      checks: {
        demo: [
          ...Array.from({ length: 4 }, (_, index) => ({
            name: `warning-${index}`,
            status: "degraded",
            detail: `warning ${index}`,
            fix: "",
          })),
          { name: "critical", status: "down", detail: "Needs repair now", fix: "Run hz system doctor" },
        ],
      },
    });
    fireEvent.click(await screen.findByRole("button", { name: "System health, error" }));
    expect(screen.getByText("Needs repair now")).toBeInTheDocument();
    expect(screen.queryByText("warning 2")).not.toBeInTheDocument();
  });
  it("limits the summary to three problems and directs remaining detail to Doctor", async () => {
    show({
      status: "degraded",
      checks: {
        demo: Array.from({ length: 5 }, (_, index) => ({
          name: `check-${index}`,
          status: "degraded",
          detail: `problem-${index}`,
          fix: "",
        })),
      },
    });
    fireEvent.click(await screen.findByRole("button", { name: /System health, warning/ }));
    expect(screen.getByText("problem-2")).toBeInTheDocument();
    expect(screen.queryByText("problem-3")).not.toBeInTheDocument();
    expect(screen.getByText("2 more checks need attention.")).toBeInTheDocument();
  });
});
