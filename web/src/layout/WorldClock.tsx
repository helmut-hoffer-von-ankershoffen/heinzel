import createGlobe from "cobe";
import { ChevronLeft, ChevronRight, Globe, X } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { SettingsShortcut } from "@/components/SettingsShortcut";
import { Button } from "@/components/ui/button";
import { WorkStateIcon } from "@/components/WorkStateIcon";
import { t } from "@/i18n";
import { workingHours } from "@/lib/workingHours";
import { initialPhi, markerPosition, nightPath, sunLocation, zoneLocation } from "@/lib/worldClock";
import { WORLD_LAND } from "@/lib/worldLand";
import { zoneClock } from "@/lib/zoneTime";
import { city, knownZone, useZones } from "./StatusBar";

/** Both a labelled time list and a globe. The globe never contacts a map service. */
export function WorldClock({ onClose, flowing = false }: { onClose: () => void; flowing?: boolean }) {
  const zones = useZones();
  const [now, setNow] = useState(() => new Date());
  const [phi, setPhi] = useState(0);
  const [reduced, setReduced] = useState(() => window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  const [failed, setFailed] = useState(false);
  const [hovered, setHovered] = useState<string | null>(null);
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
      if (document.visibilityState !== "hidden") {
        setNow(new Date());
        // COBE's embedded map texture loads asynchronously without repainting the initial empty frame.
        globeRef.current?.update({ phi: camera.current });
      }
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
    let frame: number | undefined;
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
        frame = requestAnimationFrame(() => globe?.update({ phi: camera.current }));
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
      if (frame !== undefined) cancelAnimationFrame(frame);
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
  const markers = points.flatMap((point) =>
    point.location ? [{ zone: point.zone, ...markerPosition(point.location, phi, theta) }] : [],
  );
  const hover = markers.find((point) => point.zone === hovered && point.visible);
  return (
    <section className={flowing ? "flex flex-col" : "flex h-full flex-col"} aria-label={t("worldClock.title")}>
      <header className="flex h-12 shrink-0 items-center gap-2 border-b border-border px-3">
        <Globe aria-hidden className="size-4" />
        <h2 className="flex-1 font-semibold">{t("worldClock.title")}</h2>
        <Button variant="ghost" size="icon" aria-label={t("worldClock.close")} onClick={onClose}>
          <X aria-hidden />
        </Button>
      </header>
      <div className={flowing ? "p-4" : "min-h-0 flex-1 overflow-auto p-4"}>
        {!times.length ? (
          <p className="text-sm text-muted-foreground">
            {zones.isLoading ? t("worldClock.loading") : t("worldClock.empty")}
          </p>
        ) : (
          <>
            <div className="relative aspect-square w-full" title={t("worldClock.explanation")}>
              {!flat && <div aria-hidden ref={host} className="absolute inset-0" />}
              <svg aria-hidden viewBox="0 0 100 100" className="absolute inset-0 h-full w-full">
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
                  className="absolute inset-0 touch-pan-y"
                  onPointerDown={(event) => {
                    drag.current = event.clientX;
                    event.currentTarget.setPointerCapture(event.pointerId);
                  }}
                  onPointerMove={(event) => {
                    if (drag.current === null) {
                      const rect = event.currentTarget.getBoundingClientRect();
                      const x = ((event.clientX - rect.left) / rect.width) * 100;
                      const y = ((event.clientY - rect.top) / rect.height) * 100;
                      const threshold = ((12 / rect.width) * 100) ** 2;
                      const nearest = markers
                        .filter((point) => point.visible)
                        .map((point) => ({ ...point, distance: (point.x - x) ** 2 + (point.y - y) ** 2 }))
                        .sort((a, b) => a.distance - b.distance)[0];
                      setHovered(nearest && nearest.distance <= threshold ? nearest.zone : null);
                      return;
                    }
                    setHovered(null);
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
                  onPointerLeave={() => setHovered(null)}
                />
              )}
              {!flat &&
                markers
                  .filter((point) => point.visible)
                  .map((point) => (
                    <button
                      key={point.zone}
                      type="button"
                      aria-label={`${city(point.zone)} · ${point.zone}`}
                      aria-describedby={hovered === point.zone ? `world-clock-marker-${point.zone}` : undefined}
                      className="absolute z-10 size-6 -translate-x-1/2 -translate-y-1/2 rounded-full focus-visible:outline-2 focus-visible:outline-offset-2"
                      style={{ left: `${point.x}%`, top: `${point.y}%` }}
                      onPointerEnter={() => setHovered(point.zone)}
                      onPointerMove={(event) => {
                        event.stopPropagation();
                        setHovered(point.zone);
                      }}
                      onFocus={() => setHovered(point.zone)}
                      onBlur={() => setHovered(null)}
                    />
                  ))}
              {!flat && (
                <div>
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label={t("worldClock.left")}
                    title={t("worldClock.left")}
                    className="absolute top-1/2 left-0 -translate-y-1/2"
                    onClick={() => setPhi((value) => value - Math.PI / 6)}
                  >
                    <ChevronLeft aria-hidden />
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label={t("worldClock.right")}
                    title={t("worldClock.right")}
                    className="absolute top-1/2 right-0 -translate-y-1/2"
                    onClick={() => setPhi((value) => value + Math.PI / 6)}
                  >
                    <ChevronRight aria-hidden />
                  </Button>
                </div>
              )}
              {hover && (
                <div
                  id={`world-clock-marker-${hover.zone}`}
                  role="tooltip"
                  className="pointer-events-none absolute z-(--layer-popover) -translate-x-1/2 -translate-y-full rounded-md border border-border bg-popover px-2 py-1 text-xs shadow-sm"
                  style={{ left: `${hover.x}%`, top: `${hover.y}%` }}
                >
                  {city(hover.zone)} · {hover.zone}
                </div>
              )}
            </div>
            <p className="sr-only">
              {flat ? t("worldClock.static") : t("worldClock.drag")}
              {t("worldClock.explanation")}
            </p>
            <div className="flex justify-end">
              <SettingsShortcut section="clock" label={t("status.editZones")} />
            </div>
            <ul className="divide-y divide-border">
              {times.map((zone, index) => {
                const work = workingHours(zone, now);
                return (
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
                        <span className="inline-flex items-center gap-2">
                          {city(zone.zone)}
                          {index === 0 && (
                            <span className="rounded-md border border-primary/40 px-2 py-0.5 text-[11px] text-primary">
                              {t("settings.primary")}
                            </span>
                          )}
                        </span>
                      </span>
                      {zone.zone !== city(zone.zone) && (
                        <span className="block text-xs text-muted-foreground">{zone.zone}</span>
                      )}
                    </button>
                    <WorkStateIcon work={work} />
                    {knownZone(zone.zone) ? (
                      <time dateTime={now.toISOString()} className="font-mono tabular-nums">
                        {zoneClock(now, zone.zone)}
                      </time>
                    ) : (
                      <span className="max-w-28 text-xs text-muted-foreground">{t("worldClock.unavailable")}</span>
                    )}
                  </li>
                );
              })}
            </ul>
          </>
        )}
      </div>
    </section>
  );
}
