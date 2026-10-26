// The weather plugin owns the observation and forecast. The host only presents its report in the shared side panel.
import { useQuery } from "@tanstack/react-query";
import { X } from "lucide-react";
import { token } from "@/api/token";
import { Button } from "@/components/ui/button";

type WeatherReport = {
  now: {
    location: string;
    icon: string;
    description: string;
    temperature: number;
    feels_like: number;
    high: number;
    low: number;
    rain_chance: number;
    humidity: number;
    wind_speed: number;
    units: "metric" | "imperial";
    observed_at: string;
    attribution: string;
    latitude?: number;
    longitude?: number;
  };
  hours: { at: string; icon: string; temperature: number; rain_chance: number; description: string }[];
  days: { day: string; icon: string; high: number; low: number; rain_chance: number; description: string }[];
  alerts: { event: string; sender: string; start: string; end: string; description: string }[];
};

async function report(): Promise<WeatherReport> {
  const response = await fetch(
    new Request(`${window.location.origin}/api/weather/show`, {
      headers: { Authorization: `Bearer ${token() ?? ""}` },
    }),
  );
  if (!response.ok)
    throw new Error(
      response.status === 404 ? "Weather details aren't available." : "Weather is temporarily unavailable.",
    );
  return (await response.json()) as WeatherReport;
}

const percent = (value: number) => `${Math.round(value * 100)}%`;

/** The weather provider supplies the location; embed a small OSM map with a marker without accepting a URL from it. */
export function weatherMapUrl(latitude: number, longitude: number): string | null {
  if (
    !Number.isFinite(latitude) ||
    !Number.isFinite(longitude) ||
    latitude < -90 ||
    latitude > 90 ||
    longitude < -180 ||
    longitude > 180
  )
    return null;
  const box = [longitude - 0.2, latitude - 0.12, longitude + 0.2, latitude + 0.12].join(",");
  const query = new URLSearchParams({ bbox: box, layer: "mapnik", marker: `${latitude},${longitude}` });
  return `https://www.openstreetmap.org/export/embed.html?${query}`;
}

export function WeatherPanel({ onClose }: { onClose: () => void }) {
  const query = useQuery({
    queryKey: ["weather", "report"],
    queryFn: report,
    refetchInterval: 15 * 60_000,
    retry: false,
  });
  const mapUrl = query.data
    ? weatherMapUrl(query.data.now.latitude ?? Number.NaN, query.data.now.longitude ?? Number.NaN)
    : null;
  return (
    <section aria-label="Weather" className="flex h-full min-h-0 flex-col">
      <header className="flex h-12 shrink-0 items-center justify-between border-b border-border px-4">
        <h2 className="font-semibold">Weather</h2>
        <Button variant="ghost" size="icon" onClick={onClose} aria-label="Close weather">
          <X aria-hidden />
        </Button>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        {query.isPending ? (
          <p role="status">Loading weather…</p>
        ) : query.isError ? (
          <p role="status" className="text-sm text-muted-foreground">
            {query.error.message}
          </p>
        ) : (
          <div className="flex flex-col gap-5">
            {mapUrl && (
              <section aria-label="Weather map" className="overflow-hidden rounded-xl border border-border">
                <iframe
                  title={`Weather map near ${query.data.now.location}`}
                  src={mapUrl}
                  loading="lazy"
                  referrerPolicy="no-referrer"
                  className="h-56 w-full border-0"
                />
                <p className="px-3 py-2 text-xs text-muted-foreground">
                  {query.data.now.icon} {query.data.now.description} near {query.data.now.location} · © OpenStreetMap
                  contributors
                </p>
              </section>
            )}
            <section
              aria-label={`Current weather in ${query.data.now.location}`}
              className="rounded-xl border border-border p-4"
            >
              <p className="text-sm text-muted-foreground">{query.data.now.location}</p>
              <p className="mt-2 text-3xl font-semibold">
                {query.data.now.icon} {Math.round(query.data.now.temperature)}
                {query.data.now.units === "metric" ? "°C" : "°F"}
              </p>
              <p>{query.data.now.description}</p>
              <p className="mt-2 text-sm text-muted-foreground">
                Feels like {Math.round(query.data.now.feels_like)}° · High {Math.round(query.data.now.high)}° · Low{" "}
                {Math.round(query.data.now.low)}°
              </p>
              <p className="text-sm text-muted-foreground">
                Rain {percent(query.data.now.rain_chance)} · Humidity {query.data.now.humidity}% · Wind{" "}
                {Math.round(query.data.now.wind_speed)} {query.data.now.units === "metric" ? "m/s" : "mph"}
              </p>
              <time className="mt-2 block text-xs text-muted-foreground" dateTime={query.data.now.observed_at}>
                Updated {new Date(query.data.now.observed_at).toLocaleString()}
              </time>
            </section>
            {query.data.alerts.length > 0 && (
              <section aria-label="Weather alerts" className="rounded-xl border border-warn p-3">
                <h3 className="font-semibold">Alerts</h3>
                {query.data.alerts.map((alert) => (
                  <article key={`${alert.event}-${alert.start}`} className="mt-2 text-sm">
                    <strong>{alert.event}</strong>
                    <p>{alert.description}</p>
                    <p className="text-muted-foreground">
                      {alert.sender} · until {new Date(alert.end).toLocaleString()}
                    </p>
                  </article>
                ))}
              </section>
            )}
            <section aria-label="Hourly forecast">
              <h3 className="mb-2 font-semibold">Next hours</h3>
              <ul className="divide-y divide-border rounded-xl border border-border px-3">
                {query.data.hours.map((hour) => (
                  <li key={hour.at} className="flex items-center gap-2 py-2 text-sm">
                    <time className="w-12 text-muted-foreground" dateTime={hour.at}>
                      {new Date(hour.at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                    </time>
                    <span>{hour.icon}</span>
                    <span className="flex-1">{hour.description}</span>
                    <span>{Math.round(hour.temperature)}°</span>
                    <span className="text-muted-foreground">{percent(hour.rain_chance)}</span>
                  </li>
                ))}
              </ul>
            </section>
            <section aria-label="Daily forecast">
              <h3 className="mb-2 font-semibold">Next days</h3>
              <ul className="divide-y divide-border rounded-xl border border-border px-3">
                {query.data.days.map((day) => (
                  <li key={day.day} className="flex items-center gap-2 py-2 text-sm">
                    <time className="w-20 text-muted-foreground" dateTime={day.day}>
                      {new Date(`${day.day}T12:00:00`).toLocaleDateString([], {
                        weekday: "short",
                        month: "short",
                        day: "numeric",
                      })}
                    </time>
                    <span>{day.icon}</span>
                    <span className="flex-1">{day.description}</span>
                    <span>
                      {Math.round(day.high)}° / {Math.round(day.low)}°
                    </span>
                    <span className="text-muted-foreground">{percent(day.rain_chance)}</span>
                  </li>
                ))}
              </ul>
            </section>
            {query.data.now.attribution && (
              <p className="text-xs text-muted-foreground">{query.data.now.attribution}</p>
            )}
          </div>
        )}
      </div>
    </section>
  );
}
