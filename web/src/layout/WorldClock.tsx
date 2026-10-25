import createGlobe from "cobe";
import { ChevronLeft, ChevronRight, Globe, X } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { Link } from "react-router";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n";
import { initialPhi, nightPath, sunLocation, zoneLocation } from "@/lib/worldClock";
import { WORLD_LAND } from "@/lib/worldLand";
import { zoneClock } from "@/lib/zoneTime";
import { city, knownZone, useZones } from "./StatusBar";

/** Both a labelled time list and a globe. The globe never contacts a map service. */
export function WorldClock({ onClose }: { onClose: () => void }) {
  const zones = useZones();
  const [now, setNow] = useState(() => new Date());
  const [phi, setPhi] = useState(0);
  const [reduced, setReduced] = useState(() => window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  const [failed, setFailed] = useState(false);
  const host = useRef<HTMLDivElement>(null);
  const drag = useRef<number | null>(null);
  const globeRef = useRef<ReturnType<typeof createGlobe> | null>(null);
  const clip = useId();
  const theta = 0.15;
  const times = useMemo(() => zones.data ?? [], [zones.data]);
  const points = useMemo(() => times.map((zone) => ({ zone: zone.zone, location: zoneLocation(zone.zone) })), [times]);
  const firstPoint = points[0]?.location;
  const camera = useRef(phi);
  camera.current = phi;
  useEffect(() => {
    const timer = setInterval(() => {
      if (document.visibilityState !== "hidden") setNow(new Date());
    }, 1000);
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReduced(media.matches);
    media.addEventListener("change", update);
    return () => {
      clearInterval(timer);
      media.removeEventListener("change", update);
    };
  }, []);
  useEffect(() => {
    if (firstPoint) setPhi(initialPhi(firstPoint));
    // A settings change centres the newly primary zone; ticking does not move the globe.
  }, [firstPoint]);
  useEffect(() => {
    const element = host.current;
    if (!element || reduced || failed) return;
    const canvas = document.createElement("canvas");
    canvas.style.cssText = "width:100%;height:100%;display:block";
    element.append(canvas);
    const lose = (event: Event) => {
      event.preventDefault();
      setFailed(true);
    };
    canvas.addEventListener("webglcontextlost", lose);
    let globe: ReturnType<typeof createGlobe> | undefined;
    let observer: ResizeObserver | undefined;
    try {
      // Feature detection stays local; browsers without a context get the static map.
      if (!canvas.getContext("webgl2") && !canvas.getContext("webgl")) {
        setFailed(true);
      } else {
        const size = Math.max(240, element.clientWidth);
        globe = createGlobe(canvas, {
          width: size,
          height: size,
          devicePixelRatio: Math.min(window.devicePixelRatio || 1, 2),
          phi: camera.current,
          theta,
          dark: 0,
          diffuse: 1.1,
          mapSamples: 16000,
          mapBrightness: 5,
          baseColor: [0.55, 0.65, 0.75],
          markerColor: [0.95, 0.5, 0.22],
          glowColor: [0.55, 0.65, 0.75],
          markers: points.flatMap((point, index) =>
            point.location
              ? [
                  {
                    location: point.location,
                    size: index === 0 ? 0.07 : 0.045,
                    color: (index === 0 ? [1, 0.5, 0.15] : [0.2, 0.75, 0.95]) as [number, number, number],
                  },
                ]
              : [],
          ),
        });
        globeRef.current = globe;
        observer = new ResizeObserver(() => {
          const width = Math.max(240, element.clientWidth);
          globe?.update({ width, height: width });
        });
        observer.observe(element);
      }
    } catch {
      setFailed(true);
    }
    return () => {
      observer?.disconnect();
      globe?.destroy();
      globeRef.current = null;
      canvas.removeEventListener("webglcontextlost", lose);
      element.replaceChildren();
    };
    // COBE owns this canvas and its wrapper; recreate only for settings, never each second or pointer move.
  }, [points, reduced, failed]);
  useEffect(() => {
    globeRef.current?.update({ phi });
  }, [phi]);
  const flat = reduced || failed;
  const sun = sunLocation(now);
  return (
    <section className="flex h-full flex-col" aria-label={t("worldClock.title")}>
      <header className="flex h-12 shrink-0 items-center gap-2 border-b border-border px-3">
        <Globe aria-hidden className="size-4" />
        <h2 className="flex-1 font-semibold">{t("worldClock.title")}</h2>
        <Button variant="ghost" size="icon" aria-label={t("worldClock.close")} onClick={onClose}>
          <X aria-hidden />
        </Button>
      </header>
      <div className="min-h-0 flex-1 overflow-auto p-4">
        {!times.length ? (
          <p className="text-sm text-muted-foreground">
            {zones.isLoading ? t("worldClock.loading") : t("worldClock.empty")}
          </p>
        ) : (
          <>
            <div className="relative aspect-square w-full" aria-hidden>
              {!flat && <div ref={host} className="absolute inset-0" />}
              <svg viewBox="0 0 100 100" className="absolute inset-0 h-full w-full">
                <title>{flat ? t("worldClock.map") : t("worldClock.daylight")}</title>
                <defs>
                  <clipPath id={clip}>
                    <circle cx="50" cy="50" r="40" />
                  </clipPath>
                </defs>
                {flat ? (
                  <>
                    <rect x="2" y="15" width="96" height="60" rx="3" fill="currentColor" opacity="0.07" />
                    <path d={WORLD_LAND} fill="currentColor" opacity="0.25" />
                    {[-60, -30, 0, 30, 60].map((lat) => (
                      <path
                        key={lat}
                        d={`M2 ${45 - lat / 3}h96`}
                        stroke="currentColor"
                        opacity="0.15"
                        strokeWidth="0.3"
                      />
                    ))}
                    {points.map(
                      (point, index) =>
                        point.location && (
                          <circle
                            key={point.zone}
                            cx={50 + (point.location[1] * 96) / 360}
                            cy={45 - point.location[0] / 3}
                            r={index === 0 ? 2 : 1.3}
                            fill={index === 0 ? "#ea580c" : "#0284c7"}
                          />
                        ),
                    )}
                    <circle cx={50 + (sun[1] * 96) / 360} cy={45 - sun[0] / 3} r="2" fill="#eab308" />
                  </>
                ) : (
                  <path d={nightPath(now, phi, theta)} clipPath={`url(#${clip})`} fill="#071429" opacity="0.62" />
                )}
              </svg>
              {!flat && (
                <div
                  className="absolute inset-0 touch-none"
                  onPointerDown={(event) => {
                    drag.current = event.clientX;
                    event.currentTarget.setPointerCapture(event.pointerId);
                  }}
                  onPointerMove={(event) => {
                    if (drag.current === null) return;
                    const shift = event.clientX - drag.current;
                    drag.current = event.clientX;
                    setPhi((value) => value + shift / 160);
                  }}
                  onPointerUp={() => {
                    drag.current = null;
                  }}
                  onPointerCancel={() => {
                    drag.current = null;
                  }}
                />
              )}
            </div>
            {!flat && (
              <div className="flex justify-center gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  aria-label={t("worldClock.left")}
                  onClick={() => setPhi((value) => value - Math.PI / 6)}
                >
                  <ChevronLeft aria-hidden />
                  {t("worldClock.leftLabel")}
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  aria-label={t("worldClock.right")}
                  onClick={() => setPhi((value) => value + Math.PI / 6)}
                >
                  {t("worldClock.rightLabel")}
                  <ChevronRight aria-hidden />
                </Button>
              </div>
            )}
            <p className="my-3 text-xs text-muted-foreground">
              {flat ? t("worldClock.static") : t("worldClock.drag")}
              {t("worldClock.explanation")}
            </p>
            <ul className="divide-y divide-border">
              {times.map((zone, index) => (
                <li key={zone.zone} className="flex items-center gap-3 py-3">
                  <button
                    type="button"
                    className="min-w-0 flex-1 rounded-md text-left hover:underline focus-visible:outline-2"
                    disabled={!zoneLocation(zone.zone)}
                    onClick={() => {
                      const point = zoneLocation(zone.zone);
                      if (point) setPhi(initialPhi(point));
                    }}
                  >
                    <span className="block font-medium">
                      {city(zone.zone)}
                      {index === 0 ? t("worldClock.yours") : ""}
                    </span>
                    <span className="block text-xs text-muted-foreground">
                      {zone.zone}
                      {!zoneLocation(zone.zone) ? t("worldClock.unmapped") : ""}
                    </span>
                  </button>
                  {knownZone(zone.zone) ? (
                    <time dateTime={now.toISOString()} className="font-mono tabular-nums">
                      {zoneClock(now, zone.zone)}
                    </time>
                  ) : (
                    <span className="max-w-28 text-xs text-muted-foreground">{t("worldClock.unavailable")}</span>
                  )}
                </li>
              ))}
            </ul>
          </>
        )}
        <Link to="?settings=clock" className="mt-4 inline-block text-sm underline">
          {t("worldClock.choose")}
        </Link>
      </div>
    </section>
  );
}
