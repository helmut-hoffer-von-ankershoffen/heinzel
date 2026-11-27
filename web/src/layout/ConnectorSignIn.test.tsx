import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { authorityEpoch, notifyAuthorityChange } from "@/api/token";
import * as browser from "@/lib/browser";
import { mockApi } from "@/test/render";
import { ConnectorSignIn } from "./ConnectorSignIn";

const code = "FICTIONAL-CODE";
const waiting = {
  id: "fictional-flow",
  state: "waiting",
  progress: { url: "https://example.invalid/device", code },
};

function setup() {
  vi.spyOn(browser, "isLocalServer").mockReturnValue(true);
  sessionStorage.setItem("hz.token", "fictional-authority");
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = (name: string) => (
    <QueryClientProvider client={client}>
      <ConnectorSignIn name={name} title={name} />
    </QueryClientProvider>
  );
  const rendered = render(view("Example"));
  return { client, ...rendered, provider: () => rendered.rerender(view("Other")) };
}

afterEach(() => {
  sessionStorage.clear();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it.each(["succeeded", "failed", "cancelled", "timed-out", "starting", "missing"])(
  "shows a separate code only while waiting and clears it on %s",
  async (state) => {
    let response: unknown = waiting;
    const { calls } = mockApi({
      "/api/system/connector/connect": waiting,
      "/api/system/connector/flow": () => response,
      "/api/system/connector/open": waiting,
    });
    const { client } = setup();
    const user = userEvent.setup();
    expect(screen.queryByText(code)).not.toBeInTheDocument();
    expect(calls).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "Sign in to Example" }));
    expect(await screen.findByText(code)).toBeVisible();
    expect(screen.getByText(/Enter this code on Example’s sign-in page/)).toBeVisible();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(calls.filter((call) => call.method === "POST")).toHaveLength(1);
    await user.click(screen.getByRole("button", { name: "Open Example’s sign-in page" }));
    const writes = calls.filter((call) => call.method === "POST");
    expect(await writes[0]?.json()).toEqual({ name: "Example", wait: false });
    expect(await writes[1]?.json()).toEqual({ flow: waiting.id });
    response = state === "missing" ? { status: 400, body: { error: "Unknown flow" } } : { ...waiting, state };
    await act(async () => {
      await client.invalidateQueries({ queryKey: ["system", "connector", "flow"] });
    });
    await waitFor(() => expect(screen.queryByText(code)).not.toBeInTheDocument());
    client.clear();
  },
);

it.each(["provider", "authority"])("clears the old code when %s changes", async (change) => {
  const { calls } = mockApi({
    "/api/system/connector/connect": waiting,
    "/api/system/connector/flow": waiting,
  });
  const { provider, client } = setup();
  await userEvent.setup().click(screen.getByRole("button", { name: "Sign in to Example" }));
  expect(await screen.findByText(code)).toBeVisible();
  const before = authorityEpoch();
  act(() => {
    if (change === "provider") provider();
    else {
      sessionStorage.setItem("hz.token", "fictional-replacement");
      notifyAuthorityChange();
    }
  });
  expect(screen.queryByText(code)).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Open .*sign-in page/ })).not.toBeInTheDocument();
  expect(calls.filter((call) => call.method === "POST")).toHaveLength(1);
  if (change === "authority") expect(authorityEpoch()).not.toBe(before);
  client.clear();
});

it.each([undefined, "", "   "])("does not show entry instructions for an empty code (%s)", async (empty) => {
  const flow = { ...waiting, progress: { ...waiting.progress, code: empty } };
  mockApi({
    "/api/system/connector/connect": flow,
    "/api/system/connector/flow": flow,
  });
  const { client } = setup();
  await userEvent.setup().click(screen.getByRole("button", { name: "Sign in to Example" }));
  expect(await screen.findByRole("button", { name: "Open Example’s sign-in page" })).toBeVisible();
  expect(screen.queryByText(/Enter this code/)).not.toBeInTheDocument();
  client.clear();
});
