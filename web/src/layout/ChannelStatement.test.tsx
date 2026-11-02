import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { ChannelStatement } from "./ChannelStatement";

afterEach(() => vi.unstubAllGlobals());

test("says where the next update goes", async () => {
  const fetched = vi.fn(
    async () =>
      new Response(
        JSON.stringify({ channel: "edge", changed: false, statement: "You are on edge. That update moves hz." }),
        { headers: { "content-type": "application/json" } },
      ),
  );
  vi.stubGlobal("fetch", fetched);
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ChannelStatement value="edge" />
    </QueryClientProvider>,
  );
  expect(await screen.findByText("You are on edge. That update moves hz.")).toBeTruthy();
});
