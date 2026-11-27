import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { clientHere } from "./AboutDialog";
import { RecentClients } from "./RecentClients";

afterEach(() => vi.unstubAllGlobals());

it("identifies only the current trusted bridge", () => {
  expect(clientHere({ remoteWorkspace: true })).toBe("electron");
  expect(clientHere({ clientFlavor: "native", workspaceAudio: false })).toBe("native");
});

it.each(["fresh", "stale", "missing", "unavailable"])("explains %s evidence honestly", async (state) => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        state === "unavailable"
          ? new Response("", { status: 503 })
          : new Response(
              JSON.stringify(state === "missing" ? [] : [{ kind: "unknown", platform: "unknown", presence: state }]),
            ),
      ),
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
  expect(screen.getByText(/since its last start/)).toHaveTextContent(
    "OS flavor, version and app capabilities are unknown",
  );
  expect(screen.queryByText(/Last seen|Credential access|Paired client/)).toBeNull();
  view.unmount();
  client.clear();
});
