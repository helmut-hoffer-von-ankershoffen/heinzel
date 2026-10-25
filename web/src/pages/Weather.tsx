// The weather page the weather status item links to: `hz weather` for your primary place (ADR 0056).

import { ApiError } from "@/api/client";
import { useWeather } from "@/api/queries";
import { Empty, Loaded, Page } from "@/components/common";
import { t } from "@/i18n";

const degrees = (value: number, units: string) => `${Math.round(value)}${units === "imperial" ? "°F" : "°C"}`;

export function Weather() {
  const weather = useWeather();
  // No adapter or no place is a state to act on, not a failure: say what to run.
  if (weather.error instanceof ApiError && weather.error.status !== 401) {
    return (
      <Page title={t("weather.title")} intro={t("weather.intro")}>
        <Empty
          text={weather.error.message || t("weather.none")}
          commands={["hz system config set weather.locations '[\"Berlin, DE\"]'"]}
          gnome={{ age: "baby" }}
        />
      </Page>
    );
  }
  return (
    <Page title={t("weather.title")} intro={t("weather.intro")}>
      <Loaded query={weather}>
        {(w) => (
          <>
            <section className="flex flex-col gap-1 rounded-lg border border-border p-4">
              <h2 className="text-sm text-muted-foreground">{w.location}</h2>
              <p className="text-3xl font-semibold">
                {degrees(w.temperature, w.units)} <span className="text-lg font-normal">{w.summary}</span>
              </p>
              <p className="text-sm">{t("weather.feelsLike", { value: degrees(w.feels_like, w.units) })}</p>
              <p className="text-sm text-muted-foreground">
                {t("weather.details", {
                  humidity: w.humidity_percent ?? "–",
                  wind: Math.round(w.wind),
                  speed: w.units === "imperial" ? "mph" : "km/h",
                  rain: w.precipitation,
                  length: w.units === "imperial" ? "in" : "mm",
                })}
              </p>
            </section>
            <ul className="flex flex-col gap-2">
              {w.days.map((day) => (
                <li
                  key={day.day}
                  className="flex items-baseline justify-between gap-4 rounded-md border border-border p-3"
                >
                  <span className="font-medium">
                    <time dateTime={day.day}>
                      {new Date(`${day.day}T12:00:00`).toLocaleDateString(undefined, {
                        weekday: "long",
                        day: "numeric",
                        month: "short",
                      })}
                    </time>{" "}
                    <span className="font-normal text-muted-foreground">{day.summary}</span>
                  </span>
                  <span className="text-sm">
                    {degrees(day.high, w.units)} / {degrees(day.low, w.units)}
                    {day.rain_chance !== null && (
                      <span className="ml-3 text-muted-foreground">
                        {t("weather.chance", { percent: Math.round(day.rain_chance * 100) })}
                      </span>
                    )}
                  </span>
                </li>
              ))}
            </ul>
          </>
        )}
      </Loaded>
    </Page>
  );
}
