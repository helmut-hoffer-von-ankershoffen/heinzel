import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { addressUrl, BrowserPanel, externalBrowserUrl } from "./BrowserPanel";

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
it("native controls send only explicit navigation and hide, not close, on unmount", async () => {
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
  expect(browser).toHaveBeenLastCalledWith({ action: "hide" });
  expect(browser).not.toHaveBeenCalledWith({ action: "close" });
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

it("Open in browser and Clear browsing data send their own commands, and only once a page is open", async () => {
  let url = "";
  const browser = vi.fn(async ({ action }: Record<string, unknown>) => {
    if (action === "navigate") url = "https://example.com/page";
    return { status: url ? "ready" : "empty", url };
  });
  Object.defineProperty(window, "heinzelDesktop", { value: { browser }, configurable: true });
  render(<BrowserPanel active={false} onClose={vi.fn()} />);
  const open = screen.getByRole("button", { name: "Open this link in your browser" });
  expect(open).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Clear browsing data" }));
  await waitFor(() => expect(browser).toHaveBeenCalledWith({ action: "clear" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Website address" }), {
    target: { value: "https://example.com/page" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Open this address" }));
  await waitFor(() => expect(open).toBeEnabled());
  fireEvent.click(open);
  expect(browser).toHaveBeenCalledWith({ action: "external" });
  expect(screen.getByText(/Sign-ins stay until you clear browsing data/)).toBeVisible();
});

it.each([
  ["passkey", /Passkeys may not work in the built-in browser\. Choose another sign-in option/],
  ["no-device", /No device was found for this website/],
  ["clear-failed", /Browsing data could not be cleared/],
] as const)("a %s notice from the desktop is shown as a visible alert", async (notice, text) => {
  const browser = vi.fn(async () => ({ status: "ready", url: "https://accounts.example.com/", notice }));
  Object.defineProperty(window, "heinzelDesktop", { value: { browser }, configurable: true });
  render(<BrowserPanel active={false} onClose={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "Clear browsing data" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(text);
});

it("web mode has no native browser controls", () => {
  render(<BrowserPanel active onClose={vi.fn()} />);
  expect(screen.queryByRole("button", { name: "Open this link in your browser" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Clear browsing data" })).toBeNull();
});

it("a failed Clear browsing data is shown, not hidden", async () => {
  const browser = vi.fn(async ({ action }: Record<string, unknown>) => {
    if (action === "clear") throw new Error("Browsing data could not be cleared.");
    return { status: "empty" };
  });
  Object.defineProperty(window, "heinzelDesktop", { value: { browser }, configurable: true });
  render(<BrowserPanel active={false} onClose={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "Clear browsing data" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(/Browsing data could not be cleared/);
});

it.each([
  ["google.com", "https://google.com"],
  ["www.google.com/maps", "https://www.google.com/maps"],
  ["  example.com/a?b=1  ", "https://example.com/a?b=1"],
  ["https://example.com/x", "https://example.com/x"],
  ["http://example.com/x", "http://example.com/x"],
  ["localhost", "https://localhost"],
  ["localhost:3000/app", "https://localhost:3000/app"],
  ["192.168.1.2", "https://192.168.1.2"],
  ["[::1]:8080", "https://[::1]:8080"],
  ["javascript:alert(1)", "javascript:alert(1)"],
  ["weather", "https://www.google.com/search?q=weather"],
  ["how tall is the eiffel tower", "https://www.google.com/search?q=how+tall+is+the+eiffel+tower"],
  ["what is example.com about", "https://www.google.com/search?q=what+is+example.com+about"],
  ["   ", null],
])("the address bar reads %j as %j", (input, expected) => {
  expect(addressUrl(input)).toBe(expected);
});

it("a typed host goes out with https://, and a refused local address shows a visible note", async () => {
  const browser = vi.fn(async ({ action, url }: Record<string, unknown>) => {
    if (action === "navigate" && String(url).includes("localhost"))
      throw new Error(
        "Error invoking remote method 'hz:browser': Error: Enter a public HTTPS address without credentials.",
      );
    return { status: "ready", url: action === "navigate" ? String(url) : "" };
  });
  Object.defineProperty(window, "heinzelDesktop", { value: { browser }, configurable: true });
  render(<BrowserPanel active={false} onClose={vi.fn()} />);
  const field = screen.getByRole("textbox", { name: "Website address" });
  fireEvent.change(field, { target: { value: "www.google.com/maps" } });
  fireEvent.click(screen.getByRole("button", { name: "Open this address" }));
  await waitFor(() => expect(browser).toHaveBeenCalledWith({ action: "navigate", url: "https://www.google.com/maps" }));
  fireEvent.change(field, { target: { value: "localhost" } });
  fireEvent.click(screen.getByRole("button", { name: "Open this address" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(/only public https websites work here/);
  fireEvent.change(field, { target: { value: "localhost2" } });
  expect(screen.queryByRole("alert")).toBeNull();
});

const TABS = [
  { id: "t-00000001", owner: "person", selected: true, status: "ready", url: "https://one.example.com/", title: "One" },
  { id: "t-00000002", owner: "person", selected: false, status: "ready", url: "https://two.example.com/", title: "" },
];
function tabBridge() {
  let tabs = TABS.map((tab) => ({ ...tab }));
  const select = (id: string) => {
    tabs = tabs.map((tab) => ({ ...tab, selected: tab.id === id }));
  };
  const browser = vi.fn(async (request: Record<string, unknown>) => {
    if (request.action === "select-tab") select(String(request.tab));
    if (request.action === "close-tab") {
      tabs = tabs.filter((tab) => tab.id !== request.tab);
      if (tabs[0] && !tabs.some((tab) => tab.selected)) select(tabs[0].id);
    }
    if (request.action === "new-tab") {
      tabs = [
        ...tabs.map((tab) => ({ ...tab, selected: false })),
        { id: "t-00000003", owner: "person", selected: true, status: "empty", url: "", title: "" },
      ];
    }
    const current = tabs.find((tab) => tab.selected);
    return { status: current?.status ?? "empty", url: current?.url ?? "", tabs, selected: current?.id ?? null };
  });
  Object.defineProperty(window, "heinzelDesktop", { value: { browser }, configurable: true });
  return browser;
}

it("shows the tabs the desktop holds when the panel opens again, and switches and closes them", async () => {
  const browser = tabBridge();
  render(<BrowserPanel active onClose={vi.fn()} />);
  await waitFor(() => expect(screen.getAllByRole("tab")).toHaveLength(2));
  expect(browser).toHaveBeenCalledWith({ action: "tabs" });
  expect(screen.getByRole("tab", { name: "One" })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByRole("textbox", { name: "Website address" })).toHaveValue("https://one.example.com/");
  fireEvent.click(screen.getByRole("tab", { name: "two.example.com" }));
  await waitFor(() => expect(browser).toHaveBeenCalledWith({ action: "select-tab", tab: "t-00000002" }));
  await waitFor(() =>
    expect(screen.getByRole("textbox", { name: "Website address" })).toHaveValue("https://two.example.com/"),
  );
  fireEvent.click(screen.getByRole("button", { name: "Close tab two.example.com" }));
  await waitFor(() => expect(browser).toHaveBeenCalledWith({ action: "close-tab", tab: "t-00000002" }));
  await waitFor(() => expect(screen.getAllByRole("tab")).toHaveLength(1));
});

it("a new tab opens from the plus button and from Ctrl-T; Ctrl-W closes the selected tab", async () => {
  const browser = tabBridge();
  render(<BrowserPanel active onClose={vi.fn()} />);
  await waitFor(() => expect(screen.getAllByRole("tab")).toHaveLength(2));
  fireEvent.click(screen.getByRole("button", { name: "New tab" }));
  await waitFor(() => expect(screen.getAllByRole("tab")).toHaveLength(3));
  expect(screen.getByRole("textbox", { name: "Website address" })).toHaveValue("");
  fireEvent.keyDown(screen.getByRole("textbox", { name: "Website address" }), { key: "w", ctrlKey: true });
  await waitFor(() => expect(browser).toHaveBeenCalledWith({ action: "close-tab", tab: "t-00000003" }));
  await waitFor(() => expect(screen.getAllByRole("tab")).toHaveLength(2));
  fireEvent.keyDown(screen.getByRole("textbox", { name: "Website address" }), { key: "t", ctrlKey: true });
  await waitFor(() => expect(screen.getAllByRole("tab")).toHaveLength(3));
});

it("the Mac menu's New Tab and Close Tab are taken by the panel, and Close Tab is left alone with no tab", async () => {
  const browser = tabBridge();
  render(<BrowserPanel active onClose={vi.fn()} />);
  await waitFor(() => expect(screen.getAllByRole("tab")).toHaveLength(2));
  const ask = (detail: string) => {
    const event = new CustomEvent("hz:menu", { detail, cancelable: true });
    return !window.dispatchEvent(event);
  };
  expect(ask("new-tab")).toBe(true);
  await waitFor(() => expect(screen.getAllByRole("tab")).toHaveLength(3));
  expect(ask("close-tab")).toBe(true);
  expect(ask("search")).toBe(false);
  await waitFor(() => expect(browser).toHaveBeenCalledWith({ action: "close-tab", tab: "t-00000003" }));
});

it("Ctrl-T and Ctrl-W outside the panel, with Cmd, or on macOS are left to their owners", async () => {
  const browser = tabBridge();
  render(<BrowserPanel active onClose={vi.fn()} />);
  await waitFor(() => expect(screen.getAllByRole("tab")).toHaveLength(2));
  fireEvent.keyDown(document.body, { key: "t", ctrlKey: true });
  fireEvent.keyDown(screen.getByRole("textbox", { name: "Website address" }), { key: "t", metaKey: true });
  const platform = vi.spyOn(navigator, "platform", "get").mockReturnValue("MacIntel");
  fireEvent.keyDown(screen.getByRole("textbox", { name: "Website address" }), { key: "w", ctrlKey: true });
  platform.mockRestore();
  expect(browser).not.toHaveBeenCalledWith({ action: "new-tab" });
  expect(browser).not.toHaveBeenCalledWith(expect.objectContaining({ action: "close-tab" }));
});

it("the shortcuts do nothing while another panel is in front", async () => {
  const browser = tabBridge();
  render(<BrowserPanel active={false} onClose={vi.fn()} />);
  fireEvent.keyDown(document, { key: "t", ctrlKey: true });
  expect(browser).not.toHaveBeenCalledWith({ action: "new-tab" });
});

it("a link the desktop could not open shows a note, offers Open in browser when allowed, and can be dismissed", async () => {
  let link: { url: string; reason: string; external: boolean } | null = {
    url: "https://late.example.com/",
    reason: "limit",
    external: true,
  };
  const browser = vi.fn(async ({ action }: Record<string, unknown>) => {
    if (action === "link-external" || action === "link-dismiss") link = null;
    return { status: "empty", tabs: [], selected: null, link };
  });
  Object.defineProperty(window, "heinzelDesktop", { value: { browser }, configurable: true });
  render(<BrowserPanel active onClose={vi.fn()} />);
  expect(await screen.findByText(/All 8 tabs are in use/)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Open this link in your browser" }));
  await waitFor(() => expect(browser).toHaveBeenCalledWith({ action: "link-external" }));
  await waitFor(() => expect(screen.queryByText(/All 8 tabs are in use/)).toBeNull());
});

it("a refused link without an external way out has only Dismiss", async () => {
  const browser = vi.fn(async () => ({
    status: "empty",
    tabs: [],
    selected: null,
    link: { url: "https://localhost/", reason: "refused", external: false },
  }));
  Object.defineProperty(window, "heinzelDesktop", { value: { browser }, configurable: true });
  render(<BrowserPanel active onClose={vi.fn()} />);
  expect(await screen.findByText(/public HTTPS websites only/)).toBeVisible();
  expect(screen.queryByRole("button", { name: "Open this link in your browser" })).toBeNull();
  expect(screen.getByRole("button", { name: "Dismiss" })).toBeVisible();
});
