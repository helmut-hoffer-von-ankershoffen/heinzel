// The shared SDK hosts installed factories; provider identities and routes remain declarations (ADR 0094).
import { useQuery } from "@tanstack/react-query";
import * as React from "react";
import * as ReactDOM from "react-dom";
import * as router from "react-router";
import { useLocation } from "react-router";
import { api, unwrap } from "@/api/client";
import { token } from "@/api/token";
import { type Effect, useEffectRunner } from "@/components/Contributions";
import { SettingsShortcut } from "@/components/SettingsShortcut";
import { Button } from "@/components/ui/button";
import { NotFound } from "@/pages/Profile";

export type ViewEntry = {
  id: string;
  title: string;
  fallback: string;
  slot: "page" | "panel" | "sidebar" | "settings" | "background";
  order: number;
  refresh: number;
  abi: number;
  section: string | null;
  route: string | null;
  bundle: string | null;
  styles: string[];
  actions: Record<string, Effect>;
  source: { path: string; method: string; query: Record<string, string | number | boolean> } | null;
  problem: string | null;
};

export type ViewProps = {
  data: unknown;
  loading: boolean;
  visible: boolean;
  onClose: () => void;
  flowing: boolean;
  compact: boolean;
  run: (action: string) => void;
};
export const viewSDK = { abi: 1, React, ReactDOM, router, Button, SettingsShortcut };
Reflect.set(globalThis, Symbol.for("heinzel.plugin-sdk.1"), viewSDK);
export type ViewSDK = typeof viewSDK;
export type ViewFactory = (sdk: ViewSDK) => React.ComponentType<ViewProps>;

/** Only active provider projections are returned; query failure cannot resurrect an absent provider. */
export function useViews() {
  return useQuery({
    queryKey: ["ui", "views"],
    queryFn: (): Promise<ViewEntry[]> => unwrap(api.GET("/system/ui/views")),
    refetchInterval: 60_000,
    retry: false,
  });
}

const factories = new Map<string, Promise<React.ComponentType<ViewProps>>>();

export function loadView(entry: ViewEntry): Promise<React.ComponentType<ViewProps>> {
  if (entry.abi !== viewSDK.abi || !entry.bundle || entry.problem) return Promise.reject(new Error(entry.fallback));
  const url = new URL(entry.bundle, window.location.origin);
  if (url.origin !== window.location.origin || !url.pathname.startsWith("/plugin-assets/"))
    return Promise.reject(new Error("View bundle must be an installed local asset"));
  const cached = factories.get(url.href);
  if (cached) return cached;
  const loaded = import(/* @vite-ignore */ url.href).then((module: { default?: ViewFactory }) => {
    return viewComponent(module.default);
  });
  factories.set(url.href, loaded);
  return loaded;
}

/** Validate React element types, including memo, lazy and forwardRef objects. */
export function viewComponent(factory: unknown): React.ComponentType<ViewProps> {
  if (typeof factory !== "function") throw new Error("View factory is missing");
  const component = (factory as ViewFactory)(viewSDK);
  if (typeof component === "function") return component;
  if (component && typeof component === "object") {
    const type = Reflect.get(component, "$$typeof");
    if ([Symbol.for("react.memo"), Symbol.for("react.forward_ref"), Symbol.for("react.lazy")].includes(type))
      return component;
  }
  throw new Error("View factory returned no component");
}

class ViewBoundary extends React.Component<{ fallback: string; children: React.ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? <p role="status">{this.props.fallback}</p> : this.props.children;
  }
}

export function InstalledView({
  entry,
  onClose,
  flowing = false,
  compact = false,
  visible = true,
}: {
  entry: ViewEntry;
  onClose: () => void;
  flowing?: boolean;
  compact?: boolean;
  visible?: boolean;
}) {
  const effects = useEffectRunner();
  const component = useQuery({
    queryKey: ["view-factory", entry.id, entry.bundle],
    queryFn: () => loadView(entry),
    retry: false,
  });
  const data = useQuery({
    queryKey: ["view-data", entry.id, entry.source?.path],
    enabled: Boolean(visible && entry.source && !entry.problem),
    queryFn: async (): Promise<unknown> => {
      if (entry.source?.method !== "GET") throw new Error("A view source must be read-only");
      // The server builds the source call from its owning module; factories receive values, never bearer tokens.
      const url = new URL(`/api${entry.source.path}`, window.location.origin);
      for (const [name, value] of Object.entries(entry.source.query)) url.searchParams.set(name, String(value));
      const response = await fetch(new Request(url, { headers: { Authorization: `Bearer ${token() ?? ""}` } }));
      if (!response.ok) throw new Error("View source is unavailable");
      return response.json();
    },
    refetchInterval: entry.refresh * 1000,
    retry: false,
  });
  const Component = component.data;
  if (entry.problem || component.error || data.error) return <p role="status">{entry.problem ?? entry.fallback}</p>;
  if (!Component) return <p role="status">{entry.fallback}</p>;
  return (
    <ViewBoundary key={entry.bundle} fallback={entry.fallback}>
      {(entry.styles ?? []).map((href) => (
        <link key={href} rel="stylesheet" href={href} precedence="plugin" />
      ))}
      <Component
        data={data.data}
        loading={data.isLoading}
        visible={visible}
        onClose={onClose}
        flowing={flowing}
        compact={compact}
        run={(name) => {
          const effect = entry.actions?.[name];
          if (effect) effects.run(effect);
        }}
      />
      {effects.dialog}
    </ViewBoundary>
  );
}

/** Built-in and external modules request the same slots. */
export function ViewSlot({
  slot,
  section,
  compact = false,
}: {
  slot: ViewEntry["slot"];
  section?: string;
  compact?: boolean;
}) {
  const views = useViews();
  return (
    <>
      {(views.data ?? [])
        .filter((entry) => entry.slot === slot && (!section || entry.section === section))
        .map((entry) => (
          <InstalledView key={entry.id} entry={entry} compact={compact} onClose={() => undefined} />
        ))}
    </>
  );
}

/** Routes come from active declarations; absent providers never leave an executable page. */
export function InstalledPage() {
  const views = useViews();
  const location = useLocation();
  const entry = views.data?.find((view) => view.slot === "page" && view.route === location.pathname);
  if (!entry) return views.isLoading ? <p role="status">Loading installed views…</p> : <NotFound />;
  return <InstalledView entry={entry} onClose={() => window.history.back()} flowing />;
}
