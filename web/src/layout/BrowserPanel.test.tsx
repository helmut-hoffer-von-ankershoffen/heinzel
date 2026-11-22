import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import { Providers, queryClient } from "@/app/providers";
import { mockApi } from "@/test/render";
import { addressUrl, BrowserPanel, externalBrowserUrl, urlParts } from "./BrowserPanel";

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

it("Open in browser is an icon next to the address, enabled only once a page is open", async () => {
  let url = "";
  const browser = vi.fn(async ({ action }: Record<string, unknown>) => {
    if (action === "navigate") url = "https://example.com/page";
    return { status: url ? "ready" : "empty", url };
  });
  Object.defineProperty(window, "heinzelDesktop", { value: { browser }, configurable: true });
  render(<BrowserPanel active={false} onClose={vi.fn()} />);
  expect(screen.queryByRole("button", { name: "More browser actions" })).toBeNull();
  const open = screen.getByRole("button", { name: "Open in browser" });
  expect(open).toHaveAttribute("data-heinzel-browser-external");
  expect(open).toBeDisabled();
  expect(screen.queryByRole("button", { name: "Clear browsing data" })).toBeNull();
  fireEvent.change(screen.getByRole("textbox", { name: "Website address" }), {
    target: { value: "https://example.com/page" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Open this address" }));
  await waitFor(() => expect(open).toBeEnabled());
  fireEvent.click(open);
  expect(browser).toHaveBeenCalledWith({ action: "external" });
});

it("the address shows the active page after navigation without duplicating it below the toolbar", async () => {
  const browser = vi.fn(async ({ action }: Record<string, unknown>) => {
    if (action === "navigate") return { status: "ready", url: "https://example.com/redirected" };
    return { status: "empty" };
  });
  Object.defineProperty(window, "heinzelDesktop", { value: { browser }, configurable: true });
  render(<BrowserPanel active onClose={vi.fn()} />);
  const address = screen.getByRole("textbox", { name: "Website address" });
  fireEvent.change(address, { target: { value: "example.com" } });
  expect(address).toHaveValue("example.com");
  fireEvent.click(screen.getByRole("button", { name: "Open this address" }));
  await waitFor(() => expect(address).toHaveValue("https://example.com/redirected"));
  expect(screen.queryByText("https://example.com/redirected")).toBeNull();
});

it("a completed navigation does not replace a newer address edit", async () => {
  let finishNavigation: ((result: { status: string; url: string }) => void) | undefined;
  const navigation = new Promise<{ status: string; url: string }>((resolve) => {
    finishNavigation = resolve;
  });
  // Only the typed address waits; the start page the active panel loads on its own answers at once.
  const browser = vi.fn(async ({ action, url }: Record<string, unknown>) =>
    action === "navigate" && String(url).includes("example.com") ? navigation : { status: "empty" },
  );
  Object.defineProperty(window, "heinzelDesktop", { value: { browser }, configurable: true });
  render(<BrowserPanel active onClose={vi.fn()} />);
  const address = screen.getByRole("textbox", { name: "Website address" });
  await waitFor(() => expect(browser).toHaveBeenCalledWith({ action: "navigate", url: "https://heinzel.team" }));
  fireEvent.change(address, { target: { value: "example.com" } });
  fireEvent.click(screen.getByRole("button", { name: "Open this address" }));
  fireEvent.change(address, { target: { value: "next.example.com" } });
  expect(finishNavigation).toBeDefined();
  await act(async () => finishNavigation?.({ status: "ready", url: "https://example.com/redirected" }));
  expect(address).toHaveValue("next.example.com");
});

it.each([
  ["passkey", /Passkeys may not work in the built-in browser\. Choose another sign-in option/],
  ["no-device", /No device was found for this website/],
  ["clear-failed", /Browsing data could not be cleared/],
] as const)("a %s notice from the desktop is shown as a visible alert", async (notice, text) => {
  const browser = vi.fn(async () => ({ status: "ready", url: "https://accounts.example.com/", notice }));
  Object.defineProperty(window, "heinzelDesktop", { value: { browser }, configurable: true });
  render(<BrowserPanel active={false} onClose={vi.fn()} />);
  expect(await screen.findByRole("alert")).toHaveTextContent(text);
});

it("web mode has no native browser controls", () => {
  render(<BrowserPanel active onClose={vi.fn()} />);
  expect(screen.queryByRole("button", { name: "Open in browser" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Clear browsing data" })).toBeNull();
});

it("the tab lane ends with New tab, Browser settings and Close icons, and the old more menu is gone", () => {
  const browser = vi.fn(async () => ({ status: "empty" }));
  const settings = vi.fn();
  const close = vi.fn();
  Object.defineProperty(window, "heinzelDesktop", { value: { browser }, configurable: true });
  render(<BrowserPanel active={false} onClose={close} onSettings={settings} />);
  const lane = [
    screen.getByRole("button", { name: "New tab" }),
    screen.getByRole("button", { name: "Browser settings" }),
    screen.getByRole("button", { name: "Close the browser" }),
  ];
  for (const button of lane) {
    expect(button).toHaveAttribute("title");
    expect(button.textContent).toBe("");
  }
  expect(lane[0]?.compareDocumentPosition(lane[1] as Node)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
  expect(lane[1]?.compareDocumentPosition(lane[2] as Node)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
  expect(screen.queryByRole("button", { name: "More browser actions" })).toBeNull();
  fireEvent.click(lane[1] as HTMLElement);
  expect(settings).toHaveBeenCalledOnce();
  fireEvent.click(lane[2] as HTMLElement);
  expect(close).toHaveBeenCalledOnce();
  expect(screen.getByRole("textbox", { name: "Website address" }).parentElement?.nextElementSibling).toBe(
    screen.getByRole("button", { name: "Open this address" }),
  );
});

it("loads the start page when the browser first opens with no page, and in a new tab", async () => {
  const browser = vi.fn(async ({ action }: Record<string, unknown>) =>
    action === "tabs" ? { status: "empty", tabs: [] } : { status: "loading", url: "https://start.example/" },
  );
  Object.defineProperty(window, "heinzelDesktop", { value: { browser }, configurable: true });
  render(<BrowserPanel active onClose={vi.fn()} startPage="https://start.example/" />);
  await waitFor(() => expect(browser).toHaveBeenCalledWith({ action: "navigate", url: "https://start.example/" }));
  fireEvent.click(screen.getByRole("button", { name: "New tab" }));
  await waitFor(() => expect(browser).toHaveBeenCalledWith({ action: "new-tab", url: "https://start.example/" }));
});

it("does not reload the start page over a page that is already open", async () => {
  const browser = vi.fn(async () => ({
    status: "ready",
    tabs: [{ id: "a", owner: "person", selected: true, status: "ready", url: "https://open.example/", title: "" }],
  }));
  Object.defineProperty(window, "heinzelDesktop", { value: { browser }, configurable: true });
  render(<BrowserPanel active onClose={vi.fn()} />);
  await waitFor(() => expect(browser).toHaveBeenCalledWith({ action: "tabs" }));
  expect(browser).not.toHaveBeenCalledWith(expect.objectContaining({ action: "navigate" }));
});

it("shows a refused native handoff until a new attempt, without exposing details", async () => {
  const browser = vi.fn(async () => ({ status: "ready", url: "https://example.com/" }));
  Object.defineProperty(window, "heinzelDesktop", { value: { browser }, configurable: true });
  render(<BrowserPanel active={false} onClose={vi.fn()} />);
  fireEvent(window, new Event("heinzel-guest-external-failed"));
  expect(await screen.findByRole("alert")).toHaveTextContent("This page could not be opened in your browser.");
  fireEvent.click(screen.getByRole("button", { name: "Reload the website" }));
  await waitFor(() => expect(browser).toHaveBeenCalledWith({ action: "reload" }));
  expect(screen.getByRole("alert")).not.toHaveTextContent("example.com");
  fireEvent(window, new Event("heinzel-guest-external-started"));
  expect(screen.queryByRole("alert")).toBeNull();
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
  await waitFor(() => expect(screen.getByRole("textbox", { name: "Website address" })).toHaveValue(""));
  fireEvent.keyDown(screen.getByRole("textbox", { name: "Website address" }), { key: "w", ctrlKey: true });
  await waitFor(() => expect(browser).toHaveBeenCalledWith({ action: "close-tab", tab: "t-00000003" }));
  await waitFor(() => expect(screen.getAllByRole("tab")).toHaveLength(2));
  fireEvent.keyDown(screen.getByRole("textbox", { name: "Website address" }), { key: "t", ctrlKey: true });
  await waitFor(() => expect(screen.getAllByRole("tab")).toHaveLength(3));
});

it("a delayed new-tab response clears the address restored while that request was pending", async () => {
  vi.useFakeTimers();
  try {
    const browser = tabBridge();
    const read = browser.getMockImplementation();
    if (!read) throw new Error("The fixture must supply a browser bridge");
    let restore = () => {};
    let open = () => {};
    const restored = new Promise<void>((resolve) => {
      restore = resolve;
    });
    const opened = new Promise<void>((resolve) => {
      open = resolve;
    });
    browser.mockImplementation(async (request) => {
      // A state poll can display the existing tabs before the opening tabs request returns.
      if (request.action === "tabs") {
        const snapshot = await read(request);
        await restored;
        return snapshot;
      }
      if (request.action === "new-tab") await opened;
      return read(request);
    });
    render(<BrowserPanel active onClose={vi.fn()} />);
    await act(() => vi.advanceTimersByTimeAsync(500));
    expect(screen.getAllByRole("tab")).toHaveLength(2);
    const field = screen.getByRole("textbox", { name: "Website address" });
    fireEvent.click(screen.getByRole("button", { name: "New tab" }));
    await act(async () => restore());
    await act(async () => open());
    expect(screen.getAllByRole("tab")).toHaveLength(3);
    expect(screen.getByRole("tab", { name: "New tab" })).toHaveAttribute("aria-selected", "true");
    expect(field).toHaveValue("");
    // A poll of the same selected tab must still leave an address being typed alone.
    fireEvent.change(field, { target: { value: "example.com/draft" } });
    await act(() => vi.advanceTimersByTimeAsync(500));
    expect(field).toHaveValue("example.com/draft");
  } finally {
    vi.useRealTimers();
  }
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

it("an agent's tab shows the agent's name and does not use up the person's tabs", async () => {
  const person = Array.from({ length: 7 }, (_, index) => ({
    id: `t-0000001${index}`,
    owner: "person",
    selected: index === 0,
    status: "ready",
    url: `https://p${index}.example.com/`,
    title: `Page ${index}`,
  }));
  const agent = {
    id: "t-000000aa",
    owner: "agent:chat-1",
    agent: { name: "Ada", kind: "heinzel" },
    selected: false,
    status: "ready",
    url: "https://shop.example.com/",
    title: "Shop",
  };
  const browser = vi.fn(async () => ({
    status: "ready",
    url: "https://p0.example.com/",
    tabs: [...person, agent],
    selected: "t-00000010",
  }));
  Object.defineProperty(window, "heinzelDesktop", { value: { browser }, configurable: true });
  render(<BrowserPanel active onClose={vi.fn()} />);
  await waitFor(() => expect(screen.getAllByRole("tab")).toHaveLength(8));
  expect(screen.getByRole("tab", { name: "Ada: Shop" })).toHaveAttribute("aria-selected", "false");
  expect(screen.getByRole("button", { name: "New tab" })).toBeEnabled();
});

function agentBridge(kind = "heinzel", name = "Ada") {
  const tab = {
    id: "t-000000aa",
    owner: `agent:${name}`,
    agent: { name, kind },
    selected: true,
    status: "ready",
    url: "https://shop.example.com/",
    title: "Shop",
    paused: false,
    commit: null as { summary: string; id: string } | null,
  };
  const mine = {
    id: "t-00000001",
    owner: "person",
    selected: false,
    status: "ready",
    url: "https://mine.example.com/",
    title: "Mine",
  };
  let stopped = false;
  const state = () => ({
    status: "ready",
    url: tab.url,
    tabs: stopped ? [{ ...mine, selected: true }] : [mine, { ...tab }],
    selected: stopped ? mine.id : tab.id,
  });
  const browser = vi.fn(async (request: Record<string, unknown>) => {
    if (request.action === "take-over") tab.paused = true;
    if (request.action === "hand-back") tab.paused = false;
    if (request.action === "stop-agent") stopped = true;
    if (request.action === "commit-approve" || request.action === "commit-refuse") tab.commit = null;
    return state();
  });
  Object.defineProperty(window, "heinzelDesktop", { value: { browser }, configurable: true });
  return { browser, tab };
}
const sent = (browser: ReturnType<typeof vi.fn>, action: string) =>
  browser.mock.calls
    .map(([request]) => request as Record<string, unknown>)
    .filter((request) => request.action === action);

it("an agent tab has a bar with Take over, Hand back and Stop, each for that tab only", async () => {
  const { browser, tab } = agentBridge();
  render(<BrowserPanel active onClose={vi.fn()} />);
  const bar = await screen.findByRole("group", { name: "Ada is using this tab" });
  expect(bar).toHaveTextContent("Ada is using this tab");
  expect(screen.queryByRole("button", { name: "Hand back" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Take over" }));
  expect(sent(browser, "take-over")).toEqual([{ action: "take-over", tab: tab.id }]);
  expect(await screen.findByText("You took over this tab. Ada waits until you hand it back.")).toBeVisible();
  expect(screen.queryByRole("button", { name: "Take over" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Hand back" }));
  expect(sent(browser, "hand-back")).toEqual([{ action: "hand-back", tab: tab.id }]);
  await screen.findByRole("button", { name: "Take over" });
  fireEvent.click(screen.getByRole("button", { name: "Stop" }));
  expect(sent(browser, "stop-agent")).toEqual([{ action: "stop-agent", tab: tab.id }]);
  // The agent's tabs are gone, and so is the bar.
  await waitFor(() => expect(screen.queryByRole("group", { name: /is using this tab/ })).toBeNull());
  expect(screen.queryByRole("button", { name: "Stop" })).toBeNull();
});

it("a tab the person took over by typing or clicking in the page shows Hand back without a click on Take over", async () => {
  const { tab } = agentBridge();
  tab.paused = true;
  render(<BrowserPanel active onClose={vi.fn()} />);
  expect(await screen.findByRole("button", { name: "Hand back" })).toBeVisible();
  expect(screen.queryByRole("button", { name: "Take over" })).toBeNull();
});

it("a Heinzel's tab shows the Heinzel's face and name, an outside agent's tab its name and an agent mark", async () => {
  agentBridge();
  const first = render(<BrowserPanel active onClose={vi.fn()} />);
  const heinzel = await screen.findByRole("tab", { name: "Ada: Shop" });
  expect(heinzel.querySelector("[data-agent-kind]")).toHaveAttribute("data-agent-kind", "heinzel");
  // The person's own tab carries no agent mark, and no bar when it is the one shown.
  expect(screen.getByRole("tab", { name: "Mine" }).querySelector("[data-agent-kind]")).toBeNull();
  first.unmount();
  agentBridge("external", "ci-bot");
  render(<BrowserPanel active onClose={vi.fn()} />);
  const outside = await screen.findByRole("tab", { name: "ci-bot: Shop" });
  expect(outside.querySelector("[data-agent-kind]")).toHaveAttribute("data-agent-kind", "external");
  expect(screen.getByRole("group", { name: "ci-bot is using this tab" })).toBeVisible();
});

it("the person's own tab has no agent bar and no commit card", async () => {
  const browser = vi.fn(async () => ({
    status: "ready",
    url: "https://mine.example.com/",
    selected: "t-00000001",
    tabs: [
      {
        id: "t-00000001",
        owner: "person",
        selected: true,
        status: "ready",
        url: "https://mine.example.com/",
        title: "Mine",
      },
      {
        id: "t-000000aa",
        owner: "agent:ci-bot",
        agent: { name: "ci-bot", kind: "external" },
        selected: false,
        status: "ready",
        url: "https://shop.example.com/",
        title: "Shop",
        commit: { summary: 'Click button "Buy now" on shop.example.com (browser tab t-000000aa)', id: "c".repeat(16) },
      },
    ],
  }));
  Object.defineProperty(window, "heinzelDesktop", { value: { browser }, configurable: true });
  render(<BrowserPanel active onClose={vi.fn()} />);
  // The waiting tab says so in the strip; its card shows only once the person looks at that tab.
  expect(await screen.findByRole("tab", { name: /^ci-bot: Shop\s*, waiting for your answer$/ })).toBeVisible();
  expect(screen.queryByRole("region", { name: /asks for your yes/ })).toBeNull();
  expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Take over" })).toBeNull();
});

it.each([
  ["Approve", "commit-approve"],
  ["Decline", "commit-refuse"],
])(
  "the commit card shows the action and its target unchanged, and %s sends %s for that tab",
  async (button, action) => {
    const { browser, tab } = agentBridge("external", "ci-bot");
    const summary = 'Click button "Buy now" on shop.example.com (browser tab t-000000aa)';
    tab.commit = { summary, id: "a".repeat(16) };
    render(<BrowserPanel active onClose={vi.fn()} />);
    const card = await screen.findByRole("region", { name: "ci-bot asks for your yes:" });
    expect(card).toHaveAccessibleDescription(summary);
    // Not a dialog or any other overlay the panel hides the page for: the page the person is asked about stays.
    expect(
      card.closest(
        '[role="dialog"], [role="alertdialog"], [role="menu"], [role="listbox"], [role="tooltip"], dialog, .viewport-popover, [popover]',
      ),
    ).toBeNull();
    expect(sent(browser, "commit-approve")).toEqual([]);
    expect(sent(browser, "commit-refuse")).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: button }));
    expect(sent(browser, action)).toEqual([{ action, tab: tab.id, commit: "a".repeat(16) }]);
    expect(sent(browser, action === "commit-approve" ? "commit-refuse" : "commit-approve")).toEqual([]);
    await waitFor(() => expect(screen.queryByRole("region", { name: /asks for your yes/ })).toBeNull());
  },
);

it("in the PWA there are no agent tabs, no bar and no commit card", () => {
  Object.defineProperty(window, "heinzelDesktop", { value: undefined, configurable: true });
  render(<BrowserPanel active onClose={vi.fn()} />);
  expect(screen.queryByRole("tablist")).toBeNull();
  for (const name of ["Take over", "Hand back", "Stop", "Approve", "Decline"])
    expect(screen.queryByRole("button", { name })).toBeNull();
});

it("does not show the settings help in the browser panel", () => {
  const sentence = /Some websites forbid automated use of an account/;
  Object.defineProperty(window, "heinzelDesktop", {
    value: { browser: vi.fn(async () => ({ status: "empty" })) },
    configurable: true,
  });
  const desktop = render(<BrowserPanel active onClose={vi.fn()} />);
  expect(screen.queryByText(sentence)).toBeNull();
  desktop.unmount();
  Reflect.deleteProperty(window, "heinzelDesktop");
  render(<BrowserPanel active onClose={vi.fn()} />);
  expect(screen.queryByText(sentence)).toBeNull();
});

it.each([
  ["Approve", "commit-approve"],
  ["Decline", "commit-refuse"],
])(
  "%s on a card that is no longer the tab's card, or clicked twice, never answers the next card",
  async (button, action) => {
    // The desktop as it is: an answer counts only for the card it names; then the agent's next card is there at once.
    const cardA = { summary: 'Click button "More" on shop.example.com', id: "a".repeat(16) };
    const cardB = { summary: 'Click button "Pay 900" on shop.example.com', id: "b".repeat(16) };
    let current: typeof cardA | null = cardA;
    const answered: string[] = [];
    const state = () => ({
      status: "ready",
      url: "https://shop.example.com/",
      selected: "t-000000aa",
      tabs: [
        {
          id: "t-000000aa",
          owner: "agent:ci-bot",
          agent: { name: "ci-bot", kind: "external" },
          selected: true,
          status: "ready",
          url: "https://shop.example.com/",
          title: "Shop",
          commit: current,
        },
      ],
    });
    let hold: Promise<void> | null = null;
    const browser = vi.fn(async (request: Record<string, unknown>) => {
      if (request.action === "commit-approve" || request.action === "commit-refuse") {
        if (request.commit !== current?.id) throw new Error("Unknown tab.");
        answered.push(`${request.action}:${request.commit}`);
        current = cardB;
        // The panel does not hear of card B yet: its answer is still on the way.
        if (hold) await hold;
      }
      return state();
    });
    Object.defineProperty(window, "heinzelDesktop", { value: { browser }, configurable: true });
    render(<BrowserPanel active onClose={vi.fn()} />);
    const card = await screen.findByRole("region", { name: "ci-bot asks for your yes:" });
    expect(card).toHaveAccessibleDescription(cardA.summary);
    let release = () => {};
    hold = new Promise((resolve) => {
      release = resolve;
    });
    // A double click on the card the person read: both clicks name card A.
    fireEvent.click(screen.getByRole("button", { name: button }));
    fireEvent.click(screen.getByRole("button", { name: button }));
    release();
    await waitFor(() => expect(sent(browser, action)).toHaveLength(2));
    expect(sent(browser, action).map((request) => request.commit)).toEqual([cardA.id, cardA.id]);
    expect(answered).toEqual([`${action}:${cardA.id}`]);
    // Card B is still waiting, unanswered, and it is what the panel shows next.
    expect(current).toBe(cardB);
    await waitFor(() =>
      expect(screen.getByRole("region", { name: /asks for your yes/ })).toHaveAccessibleDescription(cardB.summary),
    );
    // Now the person reads B: only this click names B.
    hold = null;
    fireEvent.click(screen.getByRole("button", { name: button }));
    await waitFor(() => expect(answered).toEqual([`${action}:${cardA.id}`, `${action}:${cardB.id}`]));
  },
);

const withTabs = () => {
  const browser = vi.fn(async () => ({
    status: "ready",
    url: "https://docs.example.com/x-api/v1?q=1",
    tabs: [
      {
        id: "a",
        owner: "person",
        selected: true,
        status: "ready",
        url: "https://docs.example.com/x-api/v1?q=1",
        title: "Docs",
      },
      { id: "b", owner: "person", selected: false, status: "ready", url: "https://news.example/", title: "News" },
    ],
    selected: "a",
  }));
  Object.defineProperty(window, "heinzelDesktop", { value: { browser }, configurable: true });
  return browser;
};

it("puts New tab right after the last tab and the expand, settings and close icons at the right end", async () => {
  withTabs();
  render(<BrowserPanel active={false} onClose={vi.fn()} onSettings={vi.fn()} onToggleFull={vi.fn()} />);
  const news = await screen.findByRole("tab", { name: "News" });
  const plus = screen.getByRole("button", { name: "New tab" });
  const expand = screen.getByRole("button", { name: "Expand the browser to full width" });
  const settings = screen.getByRole("button", { name: "Browser settings" });
  const close = screen.getByRole("button", { name: "Close the browser" });
  const follows = (a: Node, b: Node) => Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
  expect(follows(news, plus) && follows(plus, expand) && follows(expand, settings) && follows(settings, close)).toBe(
    true,
  );
  expect(plus.parentElement).toBe(screen.getByRole("tablist").parentElement);
  expect(expand.parentElement).toBe(close.parentElement);
  expect(expand.parentElement).toHaveClass("ml-auto");
  for (const icon of [plus, expand, settings, close]) expect(icon.textContent).toBe("");
});

it("expands to full width and back with a pressed state", async () => {
  withTabs();
  const toggle = vi.fn();
  const view = render(<BrowserPanel active={false} onClose={vi.fn()} onToggleFull={toggle} />);
  const expand = await screen.findByRole("button", { name: "Expand the browser to full width" });
  expect(expand).toHaveAttribute("aria-pressed", "false");
  fireEvent.click(expand);
  expect(toggle).toHaveBeenCalledOnce();
  view.rerender(<BrowserPanel active={false} onClose={vi.fn()} onToggleFull={toggle} full />);
  expect(screen.getByRole("button", { name: "Restore the browser size" })).toHaveAttribute("aria-pressed", "true");
});

it("splits an address into a bright host and a dimmed path", () => {
  expect(urlParts("https://docs.example.com/x-api/v1?q=1")).toEqual({
    host: "docs.example.com",
    rest: "/x-api/v1?q=1",
  });
  expect(urlParts("https://docs.example.com/")).toEqual({ host: "docs.example.com", rest: "" });
  expect(urlParts("not an address")).toBeNull();
});

it("shows host bright and path dimmed in a rounded field, and the full address while focused", async () => {
  withTabs();
  const { container } = render(<BrowserPanel active={false} onClose={vi.fn()} />);
  const field = await screen.findByRole("textbox", { name: "Website address" });
  await waitFor(() => expect(container.querySelector("[data-url-display]")).not.toBeNull());
  const display = container.querySelector("[data-url-display]") as HTMLElement;
  expect(display.children[0]).toHaveTextContent("docs.example.com");
  expect(display.children[0]).toHaveClass("text-foreground");
  expect(display.children[1]).toHaveTextContent("/x-api/v1?q=1");
  expect(display.children[1]).toHaveClass("text-muted-foreground");
  expect(field).toHaveClass("rounded-full", "w-full");
  expect(field).toHaveValue("https://docs.example.com/x-api/v1?q=1");
  fireEvent.focus(field);
  expect(container.querySelector("[data-url-display]")).toBeNull();
  expect(field).not.toHaveClass("text-transparent");
  fireEvent.blur(field);
  expect(container.querySelector("[data-url-display]")).not.toBeNull();
});

it("asks the Heinzel about this page: the URL and title go into the chat for the person to send", async () => {
  withTabs();
  const ask = vi.fn();
  render(<BrowserPanel active={false} onClose={vi.fn()} onAsk={ask} askName="Helmi" />);
  const button = await screen.findByRole("button", { name: "Ask Helmi about this page" });
  await waitFor(() => expect(button).toBeEnabled());
  fireEvent.click(button);
  expect(ask).toHaveBeenCalledWith("Please look at this page with me: Docs https://docs.example.com/x-api/v1?q=1");
});

it("keeps the star slot, Open in browser and Ask side by side at the end of the address row", async () => {
  withTabs();
  render(<BrowserPanel active={false} onClose={vi.fn()} onAsk={vi.fn()} askName="Helmi" />);
  const open = await screen.findByRole("button", { name: "Open in browser" });
  const ask = screen.getByRole("button", { name: "Ask Helmi about this page" });
  expect(open.nextElementSibling).toBe(ask);
  expect(screen.queryByRole("button", { name: "Ask Helmi about this page" })).toHaveAttribute(
    "data-heinzel-browser-ask",
  );
});

it.each([
  [true, 1],
  [false, 0],
] as const)("the bookmarks bar follows the setting (bar on: %s)", async (bar, shown) => {
  withTabs();
  mockApi({
    "/api/system/bookmark/list": {
      bookmarks: [
        { id: "b-1", kind: "bookmark", title: "Docs", url: "https://docs.example/", origin: null, parent: null },
      ],
      favicons: {},
    },
  });
  render(
    <Providers client={queryClient()}>
      <MemoryRouter>
        <BrowserPanel active={false} onClose={vi.fn()} bookmarks bookmarksBar={bar} />
      </MemoryRouter>
    </Providers>,
  );
  await screen.findByRole("tab", { name: "News" });
  await new Promise((resolve) => setTimeout(resolve, 50));
  expect(screen.queryAllByRole("list", { name: "Bookmarks" })).toHaveLength(shown);
});
