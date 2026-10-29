import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { base, empty } from "@/test/chat";
import { expectAccessible, mockApi, renderAt } from "@/test/render";
import { fitsBeside } from "./Shell";

const wide = window.innerWidth;

afterEach(() => {
  Object.defineProperty(window, "innerWidth", { configurable: true, value: wide });
  localStorage.clear();
  sessionStorage.clear();
});

function widthOf(px: number) {
  Object.defineProperty(window, "innerWidth", { configurable: true, value: px });
  window.dispatchEvent(new Event("resize"));
}

/** The one panel with the chat and the terminal, and where it is. */
const panel = () => document.querySelector("[data-placement]") as HTMLElement;

describe("The chat and terminal panel", () => {
  it("fits beside the screen only while the screen keeps 30rem next to its 24rem", () => {
    expect(fitsBeside(54 * 16, 16)).toBe(true);
    expect(fitsBeside(54 * 16 - 1, 16)).toBe(false);
  });

  it("is beside Home when wide, with open panels selected by tabs", async () => {
    widthOf(1400);
    mockApi(base);
    const user = userEvent.setup();
    const { container } = renderAt("/");
    expect(await screen.findByRole("heading", { name: "Today" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Open a terminal" }));
    expect(panel()).toHaveAttribute("data-placement", "beside");
    expect(
      within(screen.getByRole("tablist", { name: "Workspace panels" })).getByRole("tab", { name: "Terminal" }),
    ).toHaveAttribute("aria-selected", "true");
    expect(
      within(screen.getByRole("region", { name: "Terminal" })).getByRole("button", {
        name: "Close the terminal",
      }),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Chat" }));
    expect(screen.getByRole("complementary", { name: "Chat" })).toBeVisible();
    await expectAccessible(container);
  });

  it("goes below the screen when narrow, with one selected panel", async () => {
    widthOf(700);
    mockApi(base);
    const user = userEvent.setup();
    renderAt("/doctor");
    await user.click(await screen.findByRole("button", { name: "Open a terminal" }));
    expect(panel()).toHaveAttribute("data-placement", "below");
    expect(document.querySelectorAll("[data-placement]")).toHaveLength(1);
    expect(
      within(screen.getByRole("tablist", { name: "Workspace panels" })).getByRole("tab", { name: "Terminal" }),
    ).toHaveAttribute("aria-selected", "true");
    expect(
      within(screen.getByRole("region", { name: "Terminal" })).getByRole("button", {
        name: "Close the terminal",
      }),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Chat" }));
    expect(screen.getByRole("complementary", { name: "Chat" })).toBeVisible();
    // Getting wide again moves the same panel beside the screen.
    widthOf(1400);
    await waitFor(() => expect(panel()).toHaveAttribute("data-placement", "beside"));
  });

  it("opens with the Heinzel button, which says whether the chat is open", async () => {
    widthOf(1400);
    mockApi(base);
    const user = userEvent.setup();
    renderAt("/doctor");
    const close = await screen.findByRole("button", { name: "Close the chat", pressed: true });
    expect(close).toHaveAttribute("aria-expanded", "true");
    await user.click(close);
    expect(screen.queryByRole("complementary", { name: "Chat" })).not.toBeInTheDocument();
    const open = screen.getByRole("button", { name: "Chat with your Heinzelmännchen" });
    expect(open).toHaveAttribute("aria-pressed", "false");
    expect(open).toHaveAttribute("aria-expanded", "false");
    await user.click(open);
    expect(screen.getByRole("complementary", { name: "Chat" })).toBeInTheDocument();
  });
});

describe("The search palette", () => {
  it("closes on a click outside it", async () => {
    mockApi(base);
    const user = userEvent.setup();
    renderAt("/doctor");
    await user.click(await screen.findByRole("button", { name: "Search" }));
    const palette = await screen.findByRole("dialog", { name: /Search navigation/ });
    expect(within(palette).getByRole("combobox")).toBeInTheDocument();
    fireEvent.mouseDown(palette, { clientX: 5, clientY: 5 });
    await waitFor(() => expect(within(palette).queryByRole("combobox")).not.toBeInTheDocument());
  });
});

describe("phone workspace entry and closing", () => {
  it.each(["chat=0123456789abcdef", "chat-context=event:notice"])("selects chat for %s", async (query) => {
    widthOf(390);
    mockApi({ ...base, "/api/system/runner/chat/show": empty, "/api/system/runner/chat/open": empty });
    renderAt(`/doctor?${query}`);
    expect(await screen.findByRole("textbox", { name: "Your message" })).toBeVisible();
    expect(screen.getByRole("tab", { name: "Chat", selected: true })).toBeInTheDocument();
  });

  it("keeps the shared page active and restores it through Page without closing the terminal", async () => {
    widthOf(390);
    mockApi(base);
    const user = userEvent.setup();
    renderAt("/doctor");
    await user.click(await screen.findByRole("button", { name: "Open a terminal" }));
    const terminal = screen.getByRole("region", { name: "Terminal" });
    const main = document.getElementById("content");
    // Below the workspace, Page and the selected panel share one scroll area rather than covering each other.
    expect(main).not.toHaveAttribute("inert");
    expect(main).not.toHaveAttribute("aria-hidden");
    await user.click(screen.getByRole("tab", { name: "Page" }));
    expect(main).not.toHaveAttribute("inert");
    expect(main).not.toHaveAttribute("aria-hidden");
    await user.click(
      within(screen.getByRole("tablist", { name: "Workspace panels" })).getByRole("tab", {
        name: "Terminal",
      }),
    );
    expect(screen.getByRole("region", { name: "Terminal" })).toBe(terminal);
    await user.click(within(terminal).getByRole("button", { name: "Close the terminal" }));
    expect(screen.queryByRole("region", { name: "Terminal" })).not.toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Chat", selected: true })).toBeInTheDocument();
  });
});
