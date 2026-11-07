import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { FooterItem } from "./FooterItem";

const clients: QueryClient[] = [];
afterEach(() => {
  for (const client of clients.splice(0)) client.clear();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function LateQuery({ facts }: { facts: Promise<string> }) {
  const { data } = useQuery({ queryKey: ["footer-child-facts"], queryFn: () => facts });
  return data ? <p data-late-facts>{data}</p> : null;
}

function Example({ facts, onRender, onRun }: { facts: Promise<string>; onRender: () => void; onRun: () => void }) {
  onRender();
  return (
    <FooterItem label="Runtime facts" state="ok" text="Inspect">
      <LateQuery facts={facts} />
      <button type="button" onClick={onRun}>
        Run action
      </button>
    </FooterItem>
  );
}

it("keeps a footer action fixed at child-only query mutation delivery before resize observation", async () => {
  vi.stubGlobal(
    "visualViewport",
    Object.assign(new EventTarget(), { width: 360, height: 640, offsetLeft: 0, offsetTop: 0 }),
  );
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe = vi.fn();
      disconnect = vi.fn();
    },
  );
  let publish: (value: string) => void = () => {};
  const facts = new Promise<string>((resolve) => {
    publish = resolve;
  });
  const parentRender = vi.fn();
  const run = vi.fn();
  const measured = vi.fn();
  const disconnected = vi.spyOn(MutationObserver.prototype, "disconnect");
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  clients.push(client);
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
    if (this.tagName === "SPAN" && this.dataset.state === "ok") return new DOMRect(320, 600, 24, 24);
    const pane = this.closest(".viewport-popover") as HTMLElement | null;
    if (pane) {
      const height = pane.querySelector("[data-late-facts]") ? 194 : 150;
      const top = Number.parseFloat(pane.style.top) || 0;
      if (this === pane) {
        measured();
        return new DOMRect(0, top, 288, height);
      }
      return new DOMRect(0, top + height - 30, 100, 24);
    }
    return new DOMRect();
  });
  const user = userEvent.setup();
  const { unmount } = render(
    <QueryClientProvider client={client}>
      <Example facts={facts} onRender={parentRender} onRun={run} />
    </QueryClientProvider>,
  );
  await user.click(screen.getByRole("button", { name: "Runtime facts" }));
  const action = screen.getByRole("button", { name: "Run action" });
  await user.pointer({ target: action, keys: "[MouseLeft>]" });
  const before = action.getBoundingClientRect().y;
  const measuredBefore = measured.mock.calls.length;
  const delivery = new Promise<number>((resolve) => {
    const observer = new MutationObserver(() => {
      if (!document.querySelector("[data-late-facts]")) return;
      observer.disconnect();
      resolve(action.getBoundingClientRect().y);
    });
    observer.observe(document.body, { subtree: true, childList: true, characterData: true });
  });
  publish("Additional runtime details");
  expect(await delivery).toBe(before);
  await Promise.resolve();
  expect(measured).toHaveBeenCalledTimes(measuredBefore + 1);
  await user.pointer({ target: action, keys: "[/MouseLeft]" });
  expect(run).toHaveBeenCalledOnce();
  expect(parentRender).toHaveBeenCalledOnce();
  const disconnectedBefore = disconnected.mock.calls.length;
  unmount();
  expect(disconnected).toHaveBeenCalledTimes(disconnectedBefore + 1);
});

it("keeps a pinned footer pane above a neighbouring hover preview", async () => {
  const user = userEvent.setup();
  render(
    <>
      <FooterItem label="Calendar" state="ok" text="Calendar">
        <p>Meeting details</p>
      </FooterItem>
      <FooterItem label="Daemon" state="off" text="Daemon">
        <p>Service details</p>
      </FooterItem>
    </>,
  );
  await user.click(screen.getByRole("button", { name: "Calendar" }));
  await user.hover(screen.getByRole("button", { name: "Daemon" }));
  expect(screen.getByRole("region", { name: "Calendar" }).parentElement).toHaveClass(
    "z-[calc(var(--layer-popover,40)+1)]",
  );
  expect(screen.getByRole("region", { name: "Daemon" }).parentElement).toHaveClass("z-[var(--layer-popover,40)]");
});

it("keeps a steady footer value at a fixed digit width that never shrinks", () => {
  const { rerender } = render(<FooterItem label="Clock" state="ok" text="08:32:41" steady />);
  const text = () => screen.getByText(/\d/);
  const button = () => screen.getByRole("button");
  expect(button().className).toContain("tabular-nums");
  expect(button().className).toContain("font-mono");
  expect(text().style.minWidth).toBe("8ch");
  rerender(<FooterItem label="Clock" state="ok" text="08:32:42" steady />);
  expect(text().style.minWidth).toBe("8ch");
  rerender(<FooterItem label="Clock" state="ok" text="Mon 08:32:42" steady />);
  expect(text().style.minWidth).toBe("12ch");
  rerender(<FooterItem label="Clock" state="ok" text="08:32:43" steady />);
  expect(text().style.minWidth).toBe("12ch");
});

it("leaves an ordinary footer text item unconstrained", () => {
  render(<FooterItem label="Plain" state="ok" text="Inspect" />);
  expect(screen.getByRole("button").className).not.toContain("tabular-nums");
  expect(screen.getByText("Inspect").style.minWidth).toBe("");
});
