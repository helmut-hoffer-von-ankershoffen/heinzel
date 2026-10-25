// The sidebar's clock line: every zone of `clock.zones`, yours first, two at a time ("Berlin 09:11 · New York 03:11"),
// changing every few seconds with a fade. It rests while the pointer or focus is on it, and with reduced motion it
// shows them all at once, wrapped. One zone is shown plainly. The footer's World Clock panel is Clock in StatusBar.
import { Globe } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router";
import { useNow, useShown } from "@/components/Contributions";
import { t } from "@/i18n";
import { cn } from "@/lib/utils";
import { zoneTime } from "@/lib/zoneTime";
import { city, knownZone, useZones } from "./StatusBar";
import { CLOCK_SETTINGS } from "./settingsSections";

/** The status item this line stands for in the sidebar, so the generic list leaves it out. */
export const NATIVE_SIDEBAR = ["clock:primary-zone"];
const EVERY = 4000;
const PER_PAGE = 2;

function useReducedMotion(): boolean {
  const query = "(prefers-reduced-motion: reduce)";
  const [reduced, setReduced] = useState(() => window.matchMedia?.(query).matches ?? false);
  useEffect(() => {
    const media = window.matchMedia?.(query);
    if (!media) return;
    const update = () => setReduced(media.matches);
    media.addEventListener?.("change", update);
    return () => media.removeEventListener?.("change", update);
  }, []);
  return reduced;
}

export function ZoneTicker() {
  const shown = useShown("clock:primary-zone", "sidebar", false);
  const zones = useZones();
  const now = useNow(15_000);
  const reduced = useReducedMotion();
  const [page, setPage] = useState(0);
  const [resting, setResting] = useState(false);
  const known = (zones.data ?? []).filter((zone) => knownZone(zone.zone));
  const pages = Math.ceil(known.length / PER_PAGE);
  const rotating = pages > 1 && !reduced;
  useEffect(() => {
    if (!rotating || resting) return;
    const tick = window.setInterval(() => setPage((was) => (was + 1) % pages), EVERY);
    return () => window.clearInterval(tick);
  }, [rotating, resting, pages]);
  if (!shown || known.length === 0) return null;
  const text = (list: typeof known) => list.map((zone) => `${city(zone.zone)} ${zoneTime(now, zone.zone)}`);
  const all = text(known);
  const visible = rotating ? text(known.slice((page % pages) * PER_PAGE, (page % pages) * PER_PAGE + PER_PAGE)) : all;
  return (
    <Link
      to={`?settings=${CLOCK_SETTINGS}`}
      data-testid="zone-ticker"
      title={all.join(" · ")}
      onMouseEnter={() => setResting(true)}
      onMouseLeave={() => setResting(false)}
      onFocus={() => setResting(true)}
      onBlur={() => setResting(false)}
      className="mx-2 flex min-w-0 items-center gap-1.5 rounded px-1 py-1 text-ui-sm text-muted-foreground tabular-nums hover:bg-accent/60 hover:text-foreground"
    >
      <Globe aria-hidden className="size-3.5 shrink-0" />
      <span className="sr-only">{`${t("ticker.label")}: ${all.join(", ")}`}</span>
      <span
        aria-hidden
        key={rotating ? page : "all"}
        className={cn(
          "block min-w-0 font-mono",
          rotating ? "truncate motion-safe:animate-[hz-fade-in_0.5s_ease-out]" : "break-words",
        )}
      >
        {visible.join(" · ")}
      </span>
    </Link>
  );
}
