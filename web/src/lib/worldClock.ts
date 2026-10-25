import { ZONE_LOCATIONS } from "./zoneLocations";

const RAD = Math.PI / 180;
export type Location = [number, number];
export type Vector = [number, number, number];

/** IANA aliases known to this browser use the canonical representative point. UTC has no geographic location. */
export function zoneLocation(zone: string): Location | null {
  try {
    const canonical = new Intl.DateTimeFormat("en", { timeZone: zone }).resolvedOptions().timeZone;
    return ZONE_LOCATIONS[zone] ?? ZONE_LOCATIONS[canonical] ?? null;
  } catch {
    return null;
  }
}

/** Approximate subsolar latitude and longitude, sufficient for the daylight illustration. */
export function sunLocation(now: Date): Location {
  const day = (now.getTime() - Date.UTC(now.getUTCFullYear(), 0, 1)) / 86_400_000;
  const latitude = 23.44 * Math.sin(((day - 79) * 2 * Math.PI) / 365.2422);
  const hour = now.getUTCHours() + now.getUTCMinutes() / 60 + now.getUTCSeconds() / 3600;
  return [latitude, 180 - hour * 15];
}

/** COBE's world coordinate convention, before its camera rotation. */
export function worldPoint([latitude, longitude]: Location): Vector {
  const lat = latitude * RAD;
  const lon = longitude * RAD - Math.PI;
  return [-Math.cos(lat) * Math.cos(lon), Math.sin(lat), Math.cos(lat) * Math.sin(lon)];
}

/** Rotate a world vector into the same camera space as COBE. */
export function cameraPoint([x, y, z]: Vector, phi: number, theta: number): Vector {
  const frontX = Math.cos(phi) * x + Math.sin(phi) * z;
  const frontY = Math.sin(phi) * Math.sin(theta) * x + Math.cos(theta) * y - Math.cos(phi) * Math.sin(theta) * z;
  const frontZ = -Math.sin(phi) * Math.cos(theta) * x + Math.sin(theta) * y + Math.cos(phi) * Math.cos(theta) * z;
  return [frontX, frontY, frontZ];
}

/** Project the terminator and the dark half of the limb into COBE's camera (radius 0.8). */
export function nightPath(now: Date, phi: number, theta: number): string {
  const [sx, sy, sz] = cameraPoint(worldPoint(sunLocation(now)), phi, theta);
  const length = Math.hypot(sx, sy);
  if (length < 0.0001) return sz > 0 ? "" : "M10 50a40 40 0 1 0 80 0a40 40 0 1 0-80 0";
  const ux = -sy / length;
  const uy = sx / length;
  const vx = (-sz * sx) / length;
  const vy = (-sz * sy) / length;
  const points: string[] = [];
  const point = (x: number, y: number) => `${(50 + 40 * x).toFixed(2)} ${(50 - 40 * y).toFixed(2)}`;
  // The front half of the terminator: its z coordinate is length * sin(t), positive from zero to pi.
  for (let i = 0; i <= 64; i++) {
    const angle = (i * Math.PI) / 64;
    points.push(point(ux * Math.cos(angle) + vx * Math.sin(angle), uy * Math.cos(angle) + vy * Math.sin(angle)));
  }
  // Return along the dark limb, whose midpoint points away from the projected sun.
  for (let i = 1; i <= 64; i++) {
    const angle = (i * Math.PI) / 64;
    points.push(
      point(
        -ux * Math.cos(angle) - (sx / length) * Math.sin(angle),
        -uy * Math.cos(angle) - (sy / length) * Math.sin(angle),
      ),
    );
  }
  return `M${points.join("L")}Z`;
}

export const initialPhi = (point: Location) => -point[1] * RAD - Math.PI / 2;
