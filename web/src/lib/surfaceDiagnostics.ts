/** A bounded, URL-free snapshot for a person copying About details. */
type SurfaceName = "browser" | "player";
type Native = { focused: boolean; visible: boolean | null; status: string; reason?: string } | null;
type RecordEntry = { decision: string; beforeOverlay: string | null; bridge: string; native: Native };
const entries: Partial<Record<SurfaceName, RecordEntry>> = {};

export function recordSurface(name: SurfaceName, decision: string, bridge: string, native?: Native) {
  const previous = entries[name];
  entries[name] = {
    decision,
    beforeOverlay:
      decision === "overlay"
        ? previous?.decision === "overlay"
          ? previous.beforeOverlay
          : (previous?.decision ?? null)
        : null,
    bridge,
    native: native === undefined ? (previous?.native ?? null) : native,
  };
}

export function surfaceDetails(): string[] {
  return (["browser", "player"] as const).flatMap((name) => {
    const value = entries[name];
    if (!value) return [];
    const native = value.native;
    return [
      `${name} surface: ${value.decision}${value.beforeOverlay ? ` (before overlay: ${value.beforeOverlay})` : ""}; bridge: ${value.bridge}; native last sample: ${native ? `focused=${native.focused} view-visible=${native.visible ?? "unknown"} status=${native.status} reason=${native.reason ?? "unknown"}` : "unavailable"}`,
    ];
  });
}

/** Only same-origin, hashed build entrypoints; never include an address, query, or fragment. */
export function loadedFrontend(document: Document): string | null {
  const names = [...document.querySelectorAll<HTMLScriptElement>('script[type="module"][src]')]
    .map((script) => script.getAttribute("src") ?? "")
    .filter((path) => /^\/assets\/[A-Za-z0-9_.-]+\.js$/.test(path));
  return names.length === 1 ? (names[0] ?? null) : null;
}
