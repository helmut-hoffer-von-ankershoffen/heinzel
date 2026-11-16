import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { BrowserSettings } from "./BrowserSettings";

afterEach(() => {
  Reflect.deleteProperty(window, "heinzelDesktop");
});

it("shows the scope and clears browsing data from Browser settings", async () => {
  const browser = vi.fn(async () => ({ notice: null }));
  Object.defineProperty(window, "heinzelDesktop", { value: { browser }, configurable: true });
  render(<BrowserSettings />);
  expect(screen.getByText(/Clears browsing history and site data/)).toHaveTextContent(/signs you out/);
  expect(screen.getByText(/Bookmarks are kept/)).toBeVisible();
  expect(screen.getByText(/Some websites forbid automated use of an account/)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Clear browsing data" }));
  await waitFor(() => expect(browser).toHaveBeenCalledWith({ action: "clear" }));
  expect(await screen.findByRole("status")).toHaveTextContent("Browsing data cleared.");
});

it("reports a failed clear and allows retry", async () => {
  const browser = vi.fn().mockRejectedValueOnce(new Error("disk")).mockResolvedValue({ notice: null });
  Object.defineProperty(window, "heinzelDesktop", { value: { browser }, configurable: true });
  render(<BrowserSettings />);
  fireEvent.click(screen.getByRole("button", { name: "Clear browsing data" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(/could not be cleared/);
  fireEvent.click(screen.getByRole("button", { name: "Clear browsing data" }));
  expect(await screen.findByRole("status")).toHaveTextContent("Browsing data cleared.");
});

it("reports a native clear failure returned as a notice", async () => {
  const browser = vi.fn(async () => ({ notice: "clear-failed" }));
  Object.defineProperty(window, "heinzelDesktop", { value: { browser }, configurable: true });
  render(<BrowserSettings />);
  fireEvent.click(screen.getByRole("button", { name: "Clear browsing data" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(/could not be cleared/);
  expect(screen.queryByRole("status")).toBeNull();
  expect(screen.queryByText(/built-in browser is part of the desktop app/)).toBeNull();
});

it("does not offer desktop browsing-data controls in a web browser", () => {
  render(<BrowserSettings />);
  expect(screen.queryByRole("button", { name: "Clear browsing data" })).toBeNull();
  expect(screen.getByText(/built-in browser is part of the desktop app/)).toBeVisible();
});
