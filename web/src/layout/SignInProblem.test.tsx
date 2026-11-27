import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import * as browser from "@/lib/browser";
import { mockApi } from "@/test/render";
import { SignInProblem } from "./SignInProblem";

const expired = "gws is not signed in to Google. Sign in again: hz setup google.";
const report = {
  connectors: [
    { name: "other", title: "Other", sign_in: true, capabilities: [{ capability: "calendar", used: false }] },
    {
      name: "fictional",
      title: "Example Calendar",
      sign_in: true,
      capabilities: [{ capability: "calendar", used: true }],
    },
  ],
};

function problem(text: string, local = true) {
  vi.spyOn(browser, "isLocalServer").mockReturnValue(local);
  sessionStorage.setItem("hz.token", "fictional-session");
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <SignInProblem problem={text} />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  sessionStorage.clear();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it("starts only the server-reported calendar provider on a keyboard click and opens consent only on request", async () => {
  const waiting = { id: "fixture", state: "waiting", progress: { url: "https://example.invalid/consent" } };
  const { calls } = mockApi({
    "/api/system/connector/list": report,
    "/api/system/connector/connect": waiting,
    "/api/system/connector/flow": waiting,
    "/api/system/connector/open": waiting,
  });
  problem(expired);
  const user = userEvent.setup();
  const signIn = await screen.findByRole("button", { name: "Sign in to Example Calendar" });
  expect(calls.some((call) => call.method === "POST")).toBe(false);
  await user.tab();
  expect(signIn).toHaveFocus();
  await user.keyboard("{Enter}");
  const open = await screen.findByRole("button", { name: "Open Example Calendar’s sign-in page" });
  await waitFor(() => expect(screen.getByRole("status")).toHaveFocus());
  const writes = () => calls.filter((call) => call.method === "POST");
  expect(writes()).toHaveLength(1);
  expect(await writes()[0]?.json()).toEqual({ name: "fictional", wait: false });
  expect(writes()[0]?.headers.get("Authorization")).toBe("Bearer fictional-session");
  await user.click(open);
  expect(writes().map((call) => new URL(call.url).pathname)).toEqual([
    "/api/system/connector/connect",
    "/api/system/connector/open",
  ]);
  expect(await writes()[1]?.json()).toEqual({ flow: "fixture" });
});

it("shows the original failure details and makes start failures visible", async () => {
  const { calls } = mockApi({
    "/api/system/connector/list": report,
    "/api/system/connector/connect": { status: 503, body: { error: "Provider offline" } },
  });
  problem(expired);
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "Sign in to Example Calendar" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("could not start");
  await user.click(screen.getByText("Details"));
  expect(screen.getByText(expired)).toBeVisible();
  expect(calls.filter((call) => call.method === "POST")).toHaveLength(1);
});

it("describes where sign-in is available from another device", async () => {
  const { calls } = mockApi({ "/api/system/connector/list": report });
  problem(expired, false);
  expect(await screen.findByText("Sign in to Example Calendar on the computer running Heinzel.")).toBeVisible();
  expect(screen.queryByRole("button", { name: /sign in/i })).not.toBeInTheDocument();
  expect(calls.some((call) => call.method === "POST")).toBe(false);
});

it("keeps ordinary connectivity errors as text without looking up sign-in", () => {
  const { calls } = mockApi({});
  problem("Calendar is offline.");
  expect(screen.getByText("Calendar is offline.")).toBeVisible();
  expect(calls).toHaveLength(0);
});

it("offers a fresh sign-in when the server no longer knows the started flow", async () => {
  mockApi({
    "/api/system/connector/list": report,
    "/api/system/connector/connect": { id: "old-flow", state: "starting" },
    "/api/system/connector/flow": { status: 400, body: { error: "Unknown flow" } },
  });
  problem(expired);
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "Sign in to Example Calendar" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("no longer available");
  expect(screen.getByRole("button", { name: "Sign in to Example Calendar" })).toBeEnabled();
});
