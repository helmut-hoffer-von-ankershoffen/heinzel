import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { clientHere } from "./AboutDialog";
import { RecentClients } from "./RecentClients";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it("preserves backend identities across query refresh and reordering without duplicate keys", async () => {
  const errors = vi.spyOn(console, "error").mockImplementation(() => {});
  const device = { device_id: "device-a", labels: { machine: "Device", app_version: "1" } };
  const session = { labels: { flavor: "electron", machine: "Session", app_version: "1" } };
  const partial = { labels: { machine: "Partial" } };
  const collisionA = { device_id: "collision", labels: { machine: "Collision A" } };
  const collisionB = { device_id: "collision", labels: { machine: "Collision B" } };
  let rows: object[] = [device, {}, session, partial, collisionA, {}, collisionB];
  const fetcher = vi.fn(
    async (url: string) => new Response(JSON.stringify(url === "/api/client-observations" ? rows : [])),
  );
  vi.stubGlobal("fetch", fetcher);
  const client = new QueryClient();
  const view = render(
    <QueryClientProvider client={client}>
      <RecentClients />
    </QueryClientProvider>,
  );
  const region = within(screen.getByRole("region", { name: "Recently observed desktop apps" }));
  await region.findByText(/Device · App version 1/);
  const node = (name: string) => region.getByText(new RegExp(`${name} · App version`)).closest("li");
  const original = ["Device", "Session", "Partial", "Collision A", "Collision B"].map(node);
  rows = [
    collisionB,
    {},
    partial,
    { ...session, presence: "stale", labels: { ...session.labels, app_version: "2" } },
    {},
    collisionA,
    { ...device, last_seen: "2026-11-27T12:00:00Z", labels: { machine: "Renamed", app_version: "2" } },
  ];
  await act(async () => {
    await client.refetchQueries({ queryKey: ["web", "client-observations"] });
  });
  await region.findByText(/Renamed · App version 2/);
  ["Renamed", "Session", "Partial", "Collision A", "Collision B"].forEach((name, index) => {
    expect(node(name)).toBe(original[index]);
  });
  expect(region.getAllByRole("listitem")).toHaveLength(7);
  expect(region.getAllByRole("listitem")[0]).toBe(original[4]);
  rows = Array.from({ length: 50 }, () => ({}));
  await act(async () => {
    await client.refetchQueries({ queryKey: ["web", "client-observations"] });
  });
  await waitFor(() => expect(region.getAllByRole("listitem")).toHaveLength(50));
  const unknowns = region.getAllByRole("listitem");
  rows = [...rows].reverse();
  await act(async () => {
    await client.refetchQueries({ queryKey: ["web", "client-observations"] });
  });
  expect(region.getAllByRole("listitem")).toEqual(unknowns);
  expect(region.getAllByText("Identity: unknown")).toHaveLength(50);
  expect(fetcher.mock.calls.filter(([url]) => url === "/api/client-observations")).toHaveLength(4);
  expect(errors).not.toHaveBeenCalled();
  view.unmount();
  client.clear();
});

it("identifies only the current trusted bridge", () => {
  expect(clientHere({ remoteWorkspace: true })).toBe("electron");
  expect(clientHere({ clientFlavor: "native", workspaceAudio: false })).toBe("native");
});

it.each(["fresh", "stale", "missing", "unavailable"])("explains %s evidence honestly", async (state) => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url === "/api/client-observations") return new Response("", { status: 404 });
      if (url === "/api/recent-clients")
        return state === "unavailable"
          ? new Response("", { status: 503 })
          : new Response(
              JSON.stringify(state === "missing" ? [] : [{ kind: "unknown", platform: "unknown", presence: state }]),
            );
      throw new Error(`Unexpected route: ${url}`);
    }),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={client}>
      <RecentClients />
    </QueryClientProvider>,
  );
  const expected =
    state === "unavailable"
      ? /Client observations unavailable/
      : state === "missing"
        ? /No recent use observed/
        : state === "fresh"
          ? /^Fresh observation$/
          : /^Stale observation$/;
  expect(await screen.findByText(expected)).toBeVisible();
  expect(await screen.findByText(/This server does not support desktop observations/)).toBeVisible();
  expect(screen.getByText(/since its last start/)).toHaveTextContent(
    "OS flavor, version and app capabilities are unknown",
  );
  expect(screen.queryByText(/Last seen|Credential access|Paired client/)).toBeNull();
  view.unmount();
  client.clear();
});

it.each([
  "roster",
  "empty",
  "old",
  "error",
  "older-client",
  "unknown-identity",
  "invalid",
  "invalid-row",
  "boundary",
  "oversize",
])("reads additive desktop data: %s", async (state) => {
  const fetcher = vi.fn(async (url: string) => {
    if (url.endsWith("recent-clients")) return new Response(JSON.stringify([{ presence: "fresh" }]));
    if (url !== "/api/client-observations") throw new Error(`Unexpected route: ${url}`);
    if (state === "old") return new Response("", { status: 404 });
    if (state === "error") return new Response("", { status: 503 });
    if (state === "invalid") return new Response(JSON.stringify({ clients: [] }));
    if (state === "invalid-row") return new Response(JSON.stringify([[]]));
    if (state === "boundary" || state === "oversize")
      return new Response(JSON.stringify(Array.from({ length: state === "boundary" ? 50 : 51 }, () => ({}))));
    if (state === "unknown-identity") return new Response(JSON.stringify([{ identity_provenance: "unrecognized" }]));
    return new Response(
      JSON.stringify(
        state === "empty"
          ? []
          : state === "older-client"
            ? [{}]
            : [
                {
                  labels: { flavor: "electron", machine: "Example Mac", app_version: "0.9.57" },
                  presence: "stale",
                  last_seen: "2026-11-27T12:00:00Z",
                  identity_provenance: "device-credential",
                },
              ],
      ),
    );
  });
  vi.stubGlobal("fetch", fetcher);
  const client = new QueryClient();
  const view = render(
    <QueryClientProvider client={client}>
      <RecentClients />
    </QueryClientProvider>,
  );
  const expected =
    state === "old"
      ? /does not support desktop/
      : ["error", "invalid", "invalid-row", "oversize"].includes(state)
        ? /Desktop observations unavailable/
        : state === "boundary"
          ? /Recently observed desktop apps/
          : state === "empty"
            ? /No retained desktop observations/
            : ["older-client", "unknown-identity"].includes(state)
              ? /Unknown age observation/
              : /electron · Example Mac · App version 0.9.57/;
  expect(await screen.findByText(expected)).toBeVisible();
  expect(await screen.findByText(/^Fresh observation$/)).toBeVisible();
  if (["older-client", "unknown-identity"].includes(state)) {
    expect(screen.getByText("Identity: unknown")).toBeVisible();
    expect(screen.queryByText(/Identity: session observation/)).toBeNull();
  }
  if (state === "boundary") expect(await screen.findAllByText("Identity: unknown")).toHaveLength(50);
  if (["invalid", "invalid-row", "oversize"].includes(state)) {
    expect(screen.queryByText(/No retained desktop observations/)).toBeNull();
    expect(screen.queryByText(/Identity:/)).toBeNull();
  }
  expect(screen.getByText(/App kind, device label/)).toHaveTextContent("not an open or live connection");
  expect(fetcher).toHaveBeenCalledWith("/api/client-observations", expect.objectContaining({ cache: "no-store" }));
  view.unmount();
  client.clear();
});
