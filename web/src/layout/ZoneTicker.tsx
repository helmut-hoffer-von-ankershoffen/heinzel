// Every configured zone, primary first, on one continuous line. The duplicate makes a seamless loop; assistive
// technology gets one complete label, and reduced motion leaves a static, horizontally scrollable line.
import { Link } from "react-router";
import { useNow, useShown } from "@/components/Contributions";
import { t } from "@/i18n";
import { workingHours } from "@/lib/workingHours";
import { zoneClock } from "@/lib/zoneTime";
import { city, knownZone, useZones } from "./StatusBar";

export const NATIVE_SIDEBAR = ["clock:primary-zone"];

export function ZoneTicker() {
  const shown = useShown("clock:primary-zone", "sidebar", false);
  const zones = useZones();
  const now = useNow(1000);
  const all = (zones.data ?? []).map((zone) => {
    const time = knownZone(zone.zone) ? zoneClock(now, zone.zone) : t("worldClock.unavailable");
    return `${city(zone.zone)} ${time} · ${workingHours(zone, now).summary}`;
  });
  if (!shown || !all.length) return null;
  const label = `${t("ticker.label")}: ${all.join(" · ")}`;
  return (
    <Link
      to="?panel=world-clock"
      data-testid="zone-ticker"
      title={label}
      aria-label={label}
      className="hz-zone-ticker mx-2 block min-w-0 rounded px-1 py-1 text-ui-sm text-muted-foreground tabular-nums hover:bg-accent/60 hover:text-foreground"
    >
      <span
        aria-hidden
        className="hz-zone-track flex w-max font-mono"
        style={{ animationDuration: `${Math.max(30, all.join("").length * 0.13)}s` }}
      >
        {[0, 1].map((copy) => (
          <span key={copy} className={copy ? "hz-zone-copy flex gap-6 pr-6" : "flex gap-6 pr-6"}>
            {all.map((text, index) => (
              <span key={zones.data?.[index]?.zone}>{text}</span>
            ))}
          </span>
        ))}
      </span>
    </Link>
  );
}
