import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { Providers, queryClient } from "@/App";
import { shell } from "@/test/fixtures";
import { mockApi } from "@/test/render";
import { WeatherPanel, weatherMapUrl } from "./WeatherPanel";

const report = {
  now: {
    location: "Berlin, DE",
    latitude: 52.52,
    longitude: 13.405,
    icon: "☁️",
    description: "Partly cloudy",
    temperature: 11,
    feels_like: 10,
    high: 14,
    low: 8,
    rain_chance: 0.2,
    humidity: 65,
    wind_speed: 3,
    units: "metric",
    observed_at: "2026-10-26T12:00:00Z",
    attribution: "Weather data by Open-Meteo.com",
  },
  hours: [],
  days: [],
  alerts: [],
};

describe("WeatherPanel map", () => {
  it("centres an attributed OpenStreetMap embed on the weather coordinates", async () => {
    mockApi({ ...shell, "/api/weather/show": report });
    const { container } = render(
      <Providers client={queryClient()}>
        <MemoryRouter>
          <WeatherPanel onClose={vi.fn()} />
        </MemoryRouter>
      </Providers>,
    );
    const map = await screen.findByTitle("Weather map near Berlin, DE");
    const url = new URL(map.getAttribute("src") ?? "");
    expect(url.origin).toBe("https://www.openstreetmap.org");
    expect(url.pathname).toBe("/export/embed.html");
    expect(url.searchParams.get("marker")).toBe("52.52,13.405");
    expect(screen.getByText(/Partly cloudy near Berlin, DE/)).toBeInTheDocument();
    // jsdom cannot service axe's postMessage to a cross-origin iframe; verify this component's frame contract directly.
    expect(container.querySelector("iframe")).toHaveAttribute("referrerpolicy", "no-referrer");
  });

  it("refuses invalid coordinates and omits the map when a provider has no coordinates", async () => {
    expect(weatherMapUrl(Number.NaN, 0)).toBeNull();
    expect(weatherMapUrl(91, 0)).toBeNull();
    expect(weatherMapUrl(0, 181)).toBeNull();
    mockApi({ ...shell, "/api/weather/show": { ...report, now: { ...report.now, latitude: undefined } } });
    render(
      <Providers client={queryClient()}>
        <MemoryRouter>
          <WeatherPanel onClose={vi.fn()} />
        </MemoryRouter>
      </Providers>,
    );
    await screen.findByText("Berlin, DE");
    expect(screen.queryByLabelText("Weather map")).not.toBeInTheDocument();
  });
});
