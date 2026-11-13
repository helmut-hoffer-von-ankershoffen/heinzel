// The web build a page runs and the one the server serves: the page's hashed /assets/ entrypoints (scripts and
// stylesheets), sorted. Vite names every entrypoint after its content, so another build has other names.
import { compareCodeUnits } from "@/lib/sort";

const ASSET = /^\/assets\/[A-Za-z0-9_.-]+$/;

/** The same-origin hashed scripts and stylesheets a document loads, sorted, each once. */
export function entrypointsOf(document_: Document): string[] {
  const paths = [
    ...document_.querySelectorAll<HTMLScriptElement | HTMLLinkElement>('script[src],link[rel="stylesheet"][href]'),
  ]
    .map(
      (element) => new URL(element.getAttribute(element.tagName === "SCRIPT" ? "src" : "href") ?? "", location.origin),
    )
    .filter((url) => url.origin === location.origin && ASSET.test(url.pathname))
    .map((url) => url.pathname);
  return [...new Set(paths)].sort(compareCodeUnits);
}

/**
 * The build this page runs, read when this module loads: the app bundles every module but one script without
 * styles, so this is before any stylesheet is added at run time, and whatever the server serves since.
 */
const loaded = typeof document === "undefined" ? [] : entrypointsOf(document);

export const pageEntrypoints = (): string[] => loaded;
