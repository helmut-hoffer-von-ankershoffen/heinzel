import { readFileSync } from "node:fs";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { SchemaThemeVariant } from "@/api/client";
import { acmeTheme, shell, themeList } from "@/test/fixtures";
import { expectAccessible, mockApi, renderAt } from "@/test/render";
import { backdropCss, coloursOf, variantOf } from "./variants";

const root = () => document.documentElement.style;

const variants: SchemaThemeVariant[] = [
  { when: { daypart: ["evening", "night"], weather: [] }, light: { main_tint: "#eef0f7" }, dark: null },
  { when: { daypart: [], weather: ["rain"] }, light: { main_tint: "#e8ecef" }, dark: null },
  {
    when: { daypart: ["night"], weather: ["rain"] },
    light: {
      main_tint: "#dde3ea",
      backdrop: { gradient: "linear", angle: 180, stops: ["main_tint", "#cfd8dc"], opacity: 0.5 },
    },
    dark: null,
  },
];
const weatherTheme = { ...acmeTheme, variant: variants };

const signals = (daypart: string, weather: string | null = null) => ({
  daypart,
  weather,
  sunrise: null,
  sunset: null,
  timezone: "Europe/Berlin",
  sources: [],
  refresh: 60,
});

afterEach(() => {
  for (const name of ["--color-main-tint", "--theme-backdrop"]) root().removeProperty(name);
  document.documentElement.classList.remove("theme-shift");
  localStorage.clear();
});

describe("Variants", () => {
  it("picks the variant naming most signals, the first on a tie, and none when nothing matches", () => {
    expect(variantOf(weatherTheme, { daypart: "day" })).toBe(-1);
    expect(variantOf(weatherTheme, { daypart: "night" })).toBe(0);
    expect(variantOf(weatherTheme, { daypart: "day", weather: "rain" })).toBe(1);
    expect(variantOf(weatherTheme, { daypart: "night", weather: "rain" })).toBe(2);
    expect(variantOf({ ...weatherTheme, variant: variants.slice(0, 2) }, { daypart: "night", weather: "rain" })).toBe(
      0,
    );
    expect(coloursOf(weatherTheme, false, { daypart: "night" }).main_tint).toBe("#eef0f7");
    expect(coloursOf(weatherTheme, true, { daypart: "night" })).toEqual(acmeTheme.dark);
  });

  it("builds a backdrop from its checked parts only, and nothing from anything else", () => {
    expect(backdropCss({ gradient: "linear", angle: 90, stops: ["primary", "#fde68a"], opacity: 0.5 })).toBe(
      "linear-gradient(90deg, color-mix(in srgb, var(--color-primary) 50%, transparent), color-mix(in srgb, #fde68a 50%, transparent))",
    );
    expect(backdropCss({ gradient: "radial", angle: 0, stops: ["main_tint", "background"], opacity: 0.2 })).toMatch(
      /^radial-gradient\(circle at 50% 0%, color-mix\(in srgb, var\(--color-main-tint\) 20%/,
    );
    for (const stops of [["url(x)", "#fff"], ["#fff;}", "#000"], ["nope", "#fff"], ["#fff"]])
      expect(backdropCss({ gradient: "linear", angle: 0, stops, opacity: 0.5 })).toBe("none");
    expect(backdropCss({ gradient: "linear", angle: 0, stops: ["#fff", "#000"], opacity: 1 })).toBe("none");
    expect(backdropCss(null)).toBe("none");
  });

  it("follows the signals: the night-and-rain variant applies with its backdrop, and the change fades", async () => {
    // Asked again after `refresh` seconds: a twentieth of one here.
    let now = { ...signals("day"), refresh: 0.05 };
    mockApi({
      ...shell,
      "/api/system/theme/list": themeList("acme", [weatherTheme]),
      get "/api/system/theme/signals"() {
        return now;
      },
    });
    renderAt("/doctor");
    await waitFor(() => expect(root().getPropertyValue("--color-main-tint")).toBe("#f7f4ee"));
    expect(root().getPropertyValue("--theme-backdrop")).toBe("none");
    expect(document.documentElement.classList.contains("theme-shift")).toBe(false);
    now = { ...signals("night", "rain"), refresh: 0.05 };
    await waitFor(() => expect(root().getPropertyValue("--color-main-tint")).toBe("#dde3ea"), { timeout: 3000 });
    expect(root().getPropertyValue("--theme-backdrop")).toMatch(/^linear-gradient\(180deg/);
    expect(document.documentElement.classList.contains("theme-shift")).toBe(true);
  });

  it("applies the first variant at once when the signals arrive after the list, and a new theme never fades", async () => {
    let release: (value: unknown) => void = () => undefined;
    const late = new Promise((resolve) => {
      release = resolve;
    });
    const mocked = mockApi({ ...shell, "/api/system/theme/list": themeList("acme", [weatherTheme]) });
    void mocked;
    const fetcher = globalThis.fetch;
    vi.stubGlobal("fetch", async (input: Request) => {
      if (new URL(input.url).pathname === "/api/system/theme/signals") {
        await late;
        return Response.json(signals("night", "rain"));
      }
      return fetcher(input);
    });
    renderAt("/doctor");
    await waitFor(() => expect(root().getPropertyValue("--color-main-tint")).toBe("#f7f4ee"));
    release(undefined);
    await waitFor(() => expect(root().getPropertyValue("--color-main-tint")).toBe("#dde3ea"));
    expect(document.documentElement.classList.contains("theme-shift")).toBe(false);
  });

  it("fades only through registered colours, and reduced motion stops every transition", () => {
    const css = readFileSync("src/index.css", "utf8");
    expect(css).toMatch(/@property --color-background \{\s*syntax: "<color>";/);
    expect(css).toMatch(/html\.theme-shift \{\s*transition:/);
    expect(css).toMatch(/prefers-reduced-motion: reduce[\s\S]*transition-duration: 0\.01ms !important/);
  });

  it("lets an expert preview each variant under other conditions, without changing the app", async () => {
    const { calls } = mockApi({
      ...shell,
      "/api/system/expert": { ...shell["/api/system/expert"], expert: true },
      "/api/system/theme/list": themeList("default", [acmeTheme, weatherTheme]),
      "/api/system/theme/signals": signals("day"),
    });
    const user = userEvent.setup();
    const { container } = renderAt("/doctor?settings=hz");
    const daypart = await screen.findByRole("combobox", { name: "Time of day" });
    expect(within(daypart).getByRole("option", { name: "Daytime" })).toHaveValue("day");
    expect(screen.getByText(/These controls change the previews only/)).toBeInTheDocument();
    expect(screen.getByText(/Current weather is unavailable/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Use current conditions" })).toBeDisabled();
    const preview = () => screen.getAllByTestId("theme-preview-acme")[1] as HTMLElement;
    expect(preview().style.getPropertyValue("--color-main-tint")).toBe("#f7f4ee");
    await user.selectOptions(daypart, "night");
    await user.selectOptions(screen.getByRole("combobox", { name: "Weather" }), "rain");
    expect(preview().style.getPropertyValue("--color-main-tint")).toBe("#dde3ea");
    expect(preview().style.getPropertyValue("--theme-backdrop")).toMatch(/^linear-gradient/);
    expect(root().getPropertyValue("--color-main-tint")).toBe("");
    await user.selectOptions(screen.getByRole("combobox", { name: "Weather" }), "");
    expect(screen.getByRole("option", { name: "No weather condition", selected: true })).toBeInTheDocument();
    expect(preview().style.getPropertyValue("--color-main-tint")).toBe("#eef0f7");
    await user.click(screen.getByRole("button", { name: "Use current conditions" }));
    expect(preview().style.getPropertyValue("--color-main-tint")).toBe("#f7f4ee");
    expect(within(screen.getByRole("radiogroup", { name: "Theme" })).getAllByRole("radio")).toHaveLength(2);
    expect(calls.some((call) => call.method !== "GET")).toBe(false);
    await expectAccessible(container);
  });

  it("shows current weather separately from a deliberate no-weather preview", async () => {
    mockApi({
      ...shell,
      "/api/system/expert": { ...shell["/api/system/expert"], expert: true },
      "/api/system/theme/list": themeList("default", [weatherTheme]),
      "/api/system/theme/signals": signals("day", "rain"),
    });
    const user = userEvent.setup();
    renderAt("/doctor?settings=hz");
    const weather = await screen.findByRole("combobox", { name: "Weather" });
    await waitFor(() => expect(weather).toHaveValue("rain"));
    expect(screen.getByText("Current weather: Rain.")).toBeInTheDocument();
    await user.selectOptions(weather, "");
    expect(weather).toHaveValue("");
    expect(screen.getByText("Current weather: Rain.")).toBeInTheDocument();
    expect(screen.queryByText(/Current weather is unavailable/)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Use current conditions" }));
    expect(weather).toHaveValue("rain");
  });
});
