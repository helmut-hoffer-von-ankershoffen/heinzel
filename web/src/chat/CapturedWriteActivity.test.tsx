import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it } from "vitest";
import { mockApi } from "@/test/render";
import { CapturedWriteActivity } from "./CapturedWriteActivity";

const session = "hz-01ARZ3NDEKTSV4RRFFQ69G5FAV";
const item = {
  request_id: "draft-one",
  seq: 4,
  at: "2026-05-04T12:00:00Z",
  provider: "fixture",
  account: "owner-account",
  source: "fixture-source",
  reference: "fixture://document/one",
  status: "uncertain",
  revision: null,
};
function show() {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <CapturedWriteActivity sessionId={session} />
    </QueryClientProvider>,
  );
}

it("separates uncertain attempts from acknowledgements without provider or write calls", async () => {
  const { calls } = mockApi({
    "/api/heinzel/captured-write-activity-page": (request: Request) => {
      const older = new URL(request.url).searchParams.get("before");
      return {
        session_id: session,
        items: older ? [{ ...item, request_id: "prior", seq: 2, status: "acknowledged", revision: "r2" }] : [item],
        next_before: older ? null : 4,
      };
    },
  });
  const user = userEvent.setup();
  show();
  const panel = await screen.findByRole("region", { name: "Recorded artifact write activity" });
  expect(await within(panel).findByText("Confirmed attempt; outcome unknown")).toBeInTheDocument();
  expect(within(panel).getByText(/may have happened/)).toBeInTheDocument();
  expect(within(panel).queryByText(/Provider acknowledged/)).not.toBeInTheDocument();
  await user.click(within(panel).getByRole("button", { name: "Older activity" }));
  expect(await within(panel).findByText("Provider acknowledged the attempt")).toBeInTheDocument();
  expect(within(panel).getByText("Provider-reported revision: r2")).toBeInTheDocument();
  expect(
    calls.every(
      (request) => request.method === "GET" && new URL(request.url).pathname.endsWith("captured-write-activity-page"),
    ),
  ).toBe(true);
});

it("rejects a foreign-session response and an invented uncertain revision", async () => {
  mockApi({
    "/api/heinzel/captured-write-activity-page": () => ({ session_id: "other", items: [item], next_before: null }),
  });
  const mounted = show();
  expect(await screen.findByRole("alert")).toHaveTextContent("could not be verified");
  expect(screen.queryByText("fixture://document/one")).not.toBeInTheDocument();
  mounted.unmount();
  mockApi({
    "/api/heinzel/captured-write-activity-page": () => ({
      session_id: session,
      items: [{ ...item, revision: "invented" }],
      next_before: null,
    }),
  });
  show();
  expect(await screen.findByRole("alert")).toHaveTextContent("could not be verified");
  expect(screen.queryByText("fixture://document/one")).not.toBeInTheDocument();
});

it("discards an older session response after switching sessions", async () => {
  const other = "hz-01ARZ3NDEKTSV4RRFFQ69G5FAW";
  let complete: ((value: unknown) => void) | undefined;
  mockApi({
    "/api/heinzel/captured-write-activity-page": (request: Request) =>
      new URL(request.url).searchParams.get("session_id") === session
        ? new Promise((resolve) => {
            complete = resolve;
          })
        : { session_id: other, items: [], next_before: null },
  });
  const view = render(<CapturedWriteActivity sessionId={session} />);
  await waitFor(() => expect(complete).toBeDefined());
  view.rerender(<CapturedWriteActivity sessionId={other} />);
  expect(await screen.findByText("No recorded write activity on this page.")).toBeInTheDocument();
  complete?.({ session_id: session, items: [item], next_before: null });
  await waitFor(() => expect(screen.queryByText("fixture://document/one")).not.toBeInTheDocument());
});

it("refreshes a previously uncertain outcome without invoking a write", async () => {
  let acknowledged = false;
  const { calls } = mockApi({
    "/api/heinzel/captured-write-activity-page": () => ({
      session_id: session,
      items: [acknowledged ? { ...item, status: "acknowledged", revision: "r2" } : item],
      next_before: null,
    }),
  });
  const user = userEvent.setup();
  show();
  expect(await screen.findByText("Confirmed attempt; outcome unknown")).toBeInTheDocument();
  acknowledged = true;
  await user.click(screen.getByRole("button", { name: "Refresh activity" }));
  expect(await screen.findByText("Provider acknowledged the attempt")).toBeInTheDocument();
  expect(calls.every((request) => request.method === "GET")).toBe(true);
});
