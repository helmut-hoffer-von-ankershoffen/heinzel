import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { viewportFrame, watchViewport } from "@/lib/viewport";

type Stamp = { revision?: number; generation?: number };
type Slot = {
  x: number;
  y: number;
  width: number;
  height: number;
  viewportWidth: number;
  viewportHeight: number;
} & Stamp;
type Bridge = {
  metrics: () => { width: number; bottom: number } & Stamp;
  reportSlot: (slot: Slot) => Promise<unknown>;
};
type Metrics = { width: number; bottom: number; bridge: Bridge } & Stamp;

/** Only the newer native host advertises this contract; old apps and browsers keep their existing footer. */
export function nativeFooter(): Metrics | null {
  const bridge = (window as Window & { heinzelDesktop?: { nativeFooter?: Bridge } }).heinzelDesktop?.nativeFooter;
  if (!bridge || typeof bridge.metrics !== "function" || typeof bridge.reportSlot !== "function") return null;
  try {
    const { width, bottom, revision, generation } = bridge.metrics();
    if (!Number.isFinite(width) || !Number.isFinite(bottom)) return null;
    const stamp = Number.isSafeInteger(revision) && Number.isSafeInteger(generation) ? { revision, generation } : {};
    return { bridge, width: Math.min(180, Math.max(44, width)), bottom: Math.min(80, Math.max(0, bottom)), ...stamp };
  } catch {
    return null;
  }
}

export function useNativeFooter(): Metrics | null {
  const [metrics, setMetrics] = useState(nativeFooter);
  useEffect(() => {
    const update = () => setMetrics(nativeFooter());
    window.addEventListener("heinzel-native-footer-metrics", update);
    return () => window.removeEventListener("heinzel-native-footer-metrics", update);
  }, []);
  return metrics;
}

/** Report the actual reserved rectangle, not a guessed footer position, to the trusted native host. */
export function NativeFooterSlot({ metrics }: { metrics: Metrics }) {
  const slot = useRef<HTMLDivElement>(null);
  // biome-ignore lint/correctness/useExhaustiveDependencies: a metrics change can move the slot without resizing it
  useLayoutEffect(() => {
    const element = slot.current;
    if (!element) return;
    let last = "";
    let accepted = false;
    let pending = false;
    let attempts = 0;
    let sequence = 0;
    let active = true;
    let retry: number | undefined;
    let deadline: number | undefined;
    const report = () => {
      if (!active || !element.isConnected) return;
      const { x, y, width, height } = element.getBoundingClientRect();
      const viewport = viewportFrame();
      const stamp =
        metrics.generation === undefined ? {} : { revision: metrics.revision, generation: metrics.generation };
      const value = { x, y, width, height, viewportWidth: viewport.width, viewportHeight: viewport.height, ...stamp };
      if (!Object.values(value).every(Number.isFinite)) return;
      const next = JSON.stringify(value);
      if (next !== last) {
        last = next;
        accepted = false;
        pending = false;
        attempts = 0;
        sequence += 1;
      }
      if (accepted || pending || attempts >= 10) return;
      attempts += 1;
      pending = true;
      const request = ++sequence;
      const timedOut = window.setTimeout(() => {
        if (!active || request !== sequence) return;
        sequence += 1;
        pending = false;
        if (attempts < 10) retry = window.setTimeout(report, 200);
      }, 1000);
      deadline = timedOut;
      void Promise.resolve()
        .then(() => {
          if (!active || !element.isConnected || request !== sequence) return false;
          return metrics.bridge.reportSlot(value);
        })
        .then((result) => {
          window.clearTimeout(timedOut);
          if (!active || request !== sequence) return;
          pending = false;
          accepted = result === true;
          if (!accepted && attempts < 10) retry = window.setTimeout(report, 200);
        })
        .catch(() => {
          window.clearTimeout(timedOut);
          if (!active || request !== sequence) return;
          pending = false;
          if (attempts < 10) retry = window.setTimeout(report, 200);
        });
    };
    const refresh = () => {
      if (retry !== undefined) window.clearTimeout(retry);
      last = "";
      sequence += 1;
      pending = false;
      attempts = 0;
      report();
    };
    const observer = new ResizeObserver(report);
    observer.observe(element);
    const unwatch = watchViewport(report);
    window.addEventListener("scroll", report, true);
    window.addEventListener("hz:footer-relocated", refresh);
    window.addEventListener("heinzel-native-footer-metrics", refresh);
    report();
    return () => {
      active = false;
      if (retry !== undefined) window.clearTimeout(retry);
      if (deadline !== undefined) window.clearTimeout(deadline);
      observer.disconnect();
      unwatch();
      window.removeEventListener("scroll", report, true);
      window.removeEventListener("hz:footer-relocated", refresh);
      window.removeEventListener("heinzel-native-footer-metrics", refresh);
    };
  }, [metrics.bridge, metrics.width, metrics.bottom, metrics.revision, metrics.generation]);
  return (
    <div
      ref={slot}
      aria-hidden="true"
      data-native-footer-slot=""
      data-native-footer-version={metrics.generation === undefined ? undefined : "2"}
      className="pointer-events-none absolute top-0 left-0 h-12"
      style={{ width: metrics.width }}
    />
  );
}
