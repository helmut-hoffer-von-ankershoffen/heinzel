import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { LifecycleTransport } from "./controller";
import { RestartAndApply } from "./RestartAndApply";

const before = { instance: "old", version: "0.7.7", restartable: true };
const after = { ...before, instance: "new" };

function server() {
  return {
    read: vi.fn<LifecycleTransport["read"]>().mockResolvedValue(after).mockResolvedValueOnce(before),
    restart: vi.fn<LifecycleTransport["restart"]>().mockResolvedValue({ restarting: true, instance: before.instance }),
    verify: vi
      .fn<LifecycleTransport["verify"]>()
      .mockImplementation(async (expected) => ({ ...expected, matches: true, mismatched: [] })),
  };
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

it("shows affected target and settings, and requires explicit confirmation without losing the open draft", async () => {
  const transport = server();
  const onApplied = vi.fn();
  render(
    <>
      <textarea aria-label="Chat draft" defaultValue="Keep my unsent draft" />
      <RestartAndApply target="Clock" changes={["Primary time zone"]} options={{ transport }} onApplied={onApplied} />
    </>,
  );
  const draft = screen.getByRole("textbox", { name: "Chat draft" });
  expect(transport.read).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Restart and apply" }));
  expect(screen.getByRole("alertdialog", { name: "Restart and apply Clock" })).toBeVisible();
  expect(screen.getByText("Primary time zone")).toBeVisible();
  expect(transport.restart).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(screen.queryByRole("alertdialog")).toBeNull();
  expect(transport.read).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Restart and apply" }));
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Restart and apply Clock" })));
  expect(screen.getByRole("status")).toHaveTextContent("Applied");
  expect(onApplied).toHaveBeenCalledWith(after);
  expect(screen.getByRole("textbox", { name: "Chat draft" })).toBe(draft);
  expect(draft).toHaveValue("Keep my unsent draft");
  expect(transport.restart).toHaveBeenCalledOnce();
});

it("keeps failed verification actionable and Check again only observes the replacement", async () => {
  const transport = server();
  transport.read.mockResolvedValue(before);
  const onApplied = vi.fn();
  render(
    <RestartAndApply
      target="Clock"
      changes={["Primary time zone"]}
      options={{ transport, timeout: 50, interval: 10 }}
      onApplied={onApplied}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Restart and apply" }));
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Restart and apply Clock" })));
  expect(screen.getByRole("status")).toHaveTextContent("Waiting for the replacement server");
  await act(async () => vi.advanceTimersByTimeAsync(50));
  expect(screen.getByRole("alert")).toHaveTextContent("has not answered");
  expect(screen.queryByText("Applied")).toBeNull();
  expect(onApplied).not.toHaveBeenCalled();
  transport.read.mockResolvedValue(after);
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Check again" })));
  expect(screen.getByRole("status")).toHaveTextContent("Applied");
  expect(transport.restart).toHaveBeenCalledOnce();
});

it("cancels owned polling on unmount without claiming that the server mutation was cancelled", async () => {
  const transport = server();
  transport.read.mockResolvedValue(before);
  const onApplied = vi.fn();
  const view = render(
    <RestartAndApply
      target="Clock"
      changes={["Primary time zone"]}
      options={{ transport, interval: 10 }}
      onApplied={onApplied}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Restart and apply" }));
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Restart and apply Clock" })));
  view.unmount();
  await act(async () => vi.runAllTimersAsync());
  expect(transport.restart).toHaveBeenCalledOnce();
  expect(onApplied).not.toHaveBeenCalled();
  expect(vi.getTimerCount()).toBe(0);
});
