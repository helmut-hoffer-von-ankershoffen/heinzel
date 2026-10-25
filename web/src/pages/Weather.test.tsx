import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { shell } from "@/test/fixtures";
import { expectAccessible, mockApi, renderAt } from "@/test/render";

const outlook = {
  location: "Oslo",
  adapter: "openmeteo",
  units: "metric",
  observed_at: "2026-10-25T12:00:00+01:00",
  summary: "sunny",
  temperature: 21.4,
  feels_like: 22,
  humidity_percent: 40,
  wind: 3,
  precipitation: 0,
  days: [{ day: "2026-10-25", summary: "sunny", high: 24, low: 12, rain_chance: 0.1, precipitation: 0 }],
};

describe("Weather", () => {
  it("shows the weather now and the forecast", async () => {
    mockApi({ ...shell, "/api/weather": outlook });
    const { container } = renderAt("/weather");
    expect(await screen.findByText("Oslo")).toBeInTheDocument();
    expect(screen.getByText(/21°C/)).toBeInTheDocument();
    expect(screen.getByText("24°C / 12°C")).toBeInTheDocument();
    expect(screen.getByText("10% chance of rain")).toBeInTheDocument();
    await expectAccessible(container);
  });

  it("says what to run when there is no place or no adapter", async () => {
    mockApi({ ...shell, "/api/weather": { status: 400, body: { error: "No location is set." } } });
    renderAt("/weather");
    expect(await screen.findByText(/weather.locations/)).toBeInTheDocument();
  });
});
