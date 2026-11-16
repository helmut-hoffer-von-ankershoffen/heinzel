/** A bounded, URL-free snapshot for a person copying About details. */
type SurfaceName = "browser" | "player";
type Native = { focused: boolean; visible: boolean | null; status: string; reason?: string } | null;
type RecordEntry = { decision: string; beforeOverlay: string | null; bridge: string; native: Native };
const entries: Partial<Record<SurfaceName, RecordEntry>> = {};

export function recordSurface(name: SurfaceName, decision: string, bridge: string, native?: Native) {
  const previous = entries[name];
  let beforeOverlay: string | null = null;
  if (decision === "overlay" && previous) {
    beforeOverlay = previous.decision === "overlay" ? previous.beforeOverlay : previous.decision;
  }
  entries[name] = {
    decision,
    beforeOverlay,
    bridge,
    native: native === undefined ? (previous?.native ?? null) : native,
  };
}

export function surfaceDetails(): string[] {
  return (["browser", "player"] as const).flatMap((name) => {
    const value = entries[name];
    if (!value) return [];
    const native = value.native;
    let overlay = "";
    if (value.beforeOverlay) overlay = ` (before overlay: ${value.beforeOverlay})`;
    let sample = "unavailable";
    if (native)
      sample = `focused=${native.focused} view-visible=${native.visible ?? "unknown"} status=${native.status} reason=${native.reason ?? "unknown"}`;
    return [`${name} surface: ${value.decision}${overlay}; bridge: ${value.bridge}; native last sample: ${sample}`];
  });
}

/** Only same-origin, hashed build entrypoints; never include an address, query, or fragment. */
export function loadedFrontend(document: Document): string | null {
  const names = [...document.querySelectorAll<HTMLScriptElement>('script[type="module"][src]')]
    .map((script) => script.getAttribute("src") ?? "")
    .filter((path) => /^\/assets\/[A-Za-z0-9_.-]+\.js$/.test(path));
  return names.length === 1 ? (names[0] ?? null) : null;
}
