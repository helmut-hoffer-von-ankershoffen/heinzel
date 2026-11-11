import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, renderHook, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { mockApi } from "@/test/render";
import { HiddenWidgets, HideToast, useWidgetVisibility } from "./HiddenWidgets";

const hidden = [
  { id: "weather.card", title: "Weather", module: "weather" },
  { id: "mail.card", title: "Mail", module: "mail" },
];

describe("The list of hidden widgets", () => {
  it("is not there while nothing is hidden", () => {
    const { container } = render(<HiddenWidgets hidden={[]} restore={async () => {}} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("counts what is hidden and opens a list with a Restore per widget", async () => {
    const user = userEvent.setup();
    render(<HiddenWidgets hidden={hidden} restore={async () => {}} />);
    await user.click(screen.getByRole("button", { name: "Hidden widgets (2)" }));
    const dialog = screen.getByRole("dialog", { name: "Hidden widgets" });
    expect(dialog).toHaveTextContent("Weather");
    expect(dialog).toHaveTextContent("mail");
    expect(screen.getByRole("button", { name: "Restore Weather" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Restore Mail" })).toBeInTheDocument();
  });

  it("restores one widget or all of them", async () => {
    const user = userEvent.setup();
    const restore = vi.fn(async () => {});
    render(<HiddenWidgets hidden={hidden} restore={restore} />);
    await user.click(screen.getByRole("button", { name: "Hidden widgets (2)" }));
    await user.click(screen.getByRole("button", { name: "Restore Mail" }));
    expect(restore).toHaveBeenLastCalledWith(["mail.card"]);
    await user.click(screen.getByRole("button", { name: "Restore all" }));
    expect(restore).toHaveBeenLastCalledWith(["weather.card", "mail.card"]);
  });

  it("closes on Escape and when the person presses outside it", async () => {
    const user = userEvent.setup();
    render(
      <div>
        <button type="button">outside</button>
        <HiddenWidgets hidden={hidden} restore={async () => {}} />
      </div>,
    );
    const trigger = screen.getByRole("button", { name: "Hidden widgets (2)" });
    await user.click(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await user.click(trigger);
    await user.click(screen.getByRole("button", { name: "outside" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("closes by itself once the last widget is restored", async () => {
    const user = userEvent.setup();
    const { rerender } = render(<HiddenWidgets hidden={hidden.slice(0, 1)} restore={async () => {}} />);
    await user.click(screen.getByRole("button", { name: "Hidden widgets (1)" }));
    rerender(<HiddenWidgets hidden={[]} restore={async () => {}} />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});

describe("The toast after hiding a widget", () => {
  it("offers Undo and goes by itself after eight seconds", () => {
    vi.useFakeTimers();
    try {
      const onUndo = vi.fn();
      const onDone = vi.fn();
      render(<HideToast onUndo={onUndo} onDone={onDone} />);
      expect(screen.getByRole("status")).toHaveTextContent("Hidden.");
      screen.getByRole("button", { name: "Undo" }).click();
      expect(onUndo).toHaveBeenCalledTimes(1);
      vi.advanceTimersByTime(7999);
      expect(onDone).not.toHaveBeenCalled();
      vi.advanceTimersByTime(1);
      expect(onDone).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("Widget visibility", () => {
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      {children}
    </QueryClientProvider>
  );
  const item = (id: string, kind: string, today: boolean, byDefault: boolean) => ({
    id,
    kind,
    title: id,
    module: "m",
    slots: { today },
    defaults: { today: byDefault },
  });

  it("lists only widgets that are on by default and off now", async () => {
    mockApi({
      "/api/system/ui/list": [
        item("hidden", "widget", false, true),
        item("shown", "widget", true, true),
        item("off-by-default", "widget", false, false),
        item("not-a-widget", "page", false, true),
      ],
    });
    const { result } = renderHook(() => useWidgetVisibility(), { wrapper });
    await waitFor(() => expect(result.current.hidden.map((w) => w.id)).toEqual(["hidden"]));
  });

  it("restores widgets one call at a time, in order, on the Today slot", async () => {
    const { calls } = mockApi({
      "/api/system/ui/list": [],
      "/api/system/ui/show": {},
      "/api/system/ui/hide": {},
    });
    const { result } = renderHook(() => useWidgetVisibility(), { wrapper });
    await result.current.restore(["a", "b"]);
    await result.current.hide("c");
    const writes = calls
      .filter((request) => request.method === "POST")
      .map((request) => {
        const url = new URL(request.url);
        return `${url.pathname.split("/").pop()} ${url.searchParams.get("item")} ${url.searchParams.get("slot")}`;
      });
    expect(writes).toEqual(["show a today", "show b today", "hide c today"]);
  });
});
