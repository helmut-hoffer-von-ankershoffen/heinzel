import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { BrowserPanel, externalBrowserUrl } from "./BrowserPanel";

afterEach(() => {
  Reflect.deleteProperty(window, "heinzelDesktop");
});
it("web mode supplies an explicit safe external link without an iframe or disabled browser promise", () => {
  const { container } = render(<BrowserPanel active onClose={vi.fn()} />);
  const link = screen.getByRole("link", { name: "Open in a new browser tab" });
  expect(link).toHaveAttribute("target", "_blank");
  expect(link).toHaveAttribute("rel", "noopener noreferrer");
  expect(container.querySelector("iframe, webview")).toBeNull();
  expect(screen.getByText(/Browsing inside this panel needs the desktop app/)).toBeVisible();
  fireEvent.change(screen.getByRole("textbox", { name: "Website address" }), {
    target: { value: "javascript:alert(1)" },
  });
  expect(screen.queryByRole("link", { name: "Open in a new browser tab" })).toBeNull();
});
it.each(["file:///etc/passwd", "http://example.com", "https://user:secret@example.com", "javascript:alert(1)"])(
  "external link refuses %s",
  (value) => {
    expect(externalBrowserUrl(value)).toBeNull();
  },
);
it("native controls send only explicit navigation and close the separate session on unmount", async () => {
  const browser = vi.fn(async ({ action }: Record<string, unknown>) =>
    action === "navigate" ? { status: "ready", url: "https://example.com/page" } : { status: "empty" },
  );
  Object.defineProperty(window, "heinzelDesktop", { value: { browser }, configurable: true });
  const view = render(<BrowserPanel active={false} onClose={vi.fn()} />);
  expect(browser).toHaveBeenCalledWith({ action: "hide" });
  expect(browser).not.toHaveBeenCalledWith(expect.objectContaining({ action: "navigate" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Website address" }), {
    target: { value: "https://example.com/page" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Open this address" }));
  await waitFor(() => expect(browser).toHaveBeenCalledWith({ action: "navigate", url: "https://example.com/page" }));
  expect(screen.getByRole("button", { name: "Back" })).toBeDisabled();
  const reload = screen.getByRole("button", { name: "Reload the website" });
  await waitFor(() => expect(reload).toBeEnabled());
  fireEvent.click(reload);
  expect(browser).toHaveBeenCalledWith({ action: "reload" });
  view.unmount();
  expect(browser).toHaveBeenCalledWith({ action: "close" });
});

it("switching away from the panel hides the page without closing it, so its state survives", async () => {
  const browser = vi.fn(async ({ action }: Record<string, unknown>) =>
    action === "navigate" ? { status: "ready", url: "https://example.com/page" } : { status: "ready" },
  );
  Object.defineProperty(window, "heinzelDesktop", { value: { browser }, configurable: true });
  const view = render(<BrowserPanel active onClose={vi.fn()} />);
  fireEvent.change(screen.getByRole("textbox", { name: "Website address" }), {
    target: { value: "https://example.com/page" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Open this address" }));
  await waitFor(() => expect(browser).toHaveBeenCalledWith({ action: "navigate", url: "https://example.com/page" }));
  view.rerender(<BrowserPanel active={false} onClose={vi.fn()} />);
  await waitFor(() => expect(browser).toHaveBeenLastCalledWith({ action: "hide" }));
  view.rerender(<BrowserPanel active onClose={vi.fn()} />);
  expect(browser).not.toHaveBeenCalledWith({ action: "close" });
  expect(screen.getByRole("textbox", { name: "Website address" })).toHaveValue("https://example.com/page");
});

it("older desktop shells without the browser bridge keep the external-link fallback", () => {
  Object.defineProperty(window, "heinzelDesktop", { value: { lifecycle: vi.fn() }, configurable: true });
  render(<BrowserPanel active onClose={vi.fn()} />);
  expect(screen.getByRole("link", { name: "Open in a new browser tab" })).toBeVisible();
  expect(screen.queryByRole("button", { name: "Open this address" })).toBeNull();
});

it("hides the native surface while a product popover is open and restores it when dismissed", async () => {
  const browser = vi.fn(async () => ({ status: "ready" }));
  Object.defineProperty(window, "heinzelDesktop", { value: { browser }, configurable: true });
  const bounds = new DOMRect(10, 20, 100, 200);
  const measure = vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(bounds);
  try {
    const view = render(<BrowserPanel active onClose={vi.fn()} />);
    const layout = { action: "layout", bounds: { x: 10, y: 20, width: 100, height: 200 } };
    expect(browser).toHaveBeenLastCalledWith(layout);

    const popover = document.createElement("div");
    popover.className = "viewport-popover";
    Object.defineProperty(popover, "getClientRects", { value: () => [bounds] });
    view.container.append(popover);
    await waitFor(() => expect(browser).toHaveBeenLastCalledWith({ action: "hide" }));

    popover.remove();
    await waitFor(() => expect(browser).toHaveBeenLastCalledWith(layout));
  } finally {
    measure.mockRestore();
  }
});
