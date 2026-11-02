import { afterEach, describe, expect, it, vi } from "vitest";
import { followTerminalTheme, terminalTheme } from "./terminalTheme";

const root = document.documentElement;
afterEach(() => {
  root.className = "";
  root.removeAttribute("style");
  vi.useRealTimers();
});

describe("terminalTheme", () => {
  it("takes background, foreground, cursor and ANSI colours from the theme's tokens", () => {
    root.style.setProperty("--color-background", "#102030");
    root.style.setProperty("--color-foreground", "#f0e0d0");
    root.style.setProperty("--color-primary", "#33aa44");
    root.style.setProperty("--color-bad", "#ff0000");
    const theme = terminalTheme();
    expect(theme.background).toBe("#102030");
    expect(theme.foreground).toBe("#f0e0d0");
    expect(theme.cursor).toBe("#33aa44");
    expect(theme.selectionBackground).toBe("#33aa4455");
    expect(theme.red).toBe("#ff0000");
  });

  it("has the base theme's colours without tokens, light and dark, and all sixteen ANSI colours", () => {
    expect(terminalTheme().background).toBe("#faf8f3");
    root.classList.add("dark");
    const dark = terminalTheme();
    expect(dark.background).toBe("#18231e");
    expect(dark.blue).toBe("#7aa7f0");
    const ansi = ["black", "red", "green", "yellow", "blue", "magenta", "cyan", "white"];
    for (const name of ansi) {
      expect(dark[name as keyof typeof dark]).toMatch(/^#[0-9a-f]{6}$/);
      const bright = `bright${name[0]?.toUpperCase()}${name.slice(1)}`;
      expect(dark[bright as keyof typeof dark]).toMatch(/^#[0-9a-f]{6}$/);
    }
  });

  it("applies again when the theme's tokens or the mode change", async () => {
    const seen: (string | undefined)[] = [];
    const stop = followTerminalTheme((theme) => seen.push(theme.background));
    root.style.setProperty("--color-background", "#abcdef");
    await vi.waitFor(() => expect(seen).toContain("#abcdef"));
    root.classList.add("dark");
    root.style.removeProperty("--color-background");
    await vi.waitFor(() => expect(seen.at(-1)).toBe("#18231e"));
    stop();
  });
});
