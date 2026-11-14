// The header's state: face and word agree while a turn runs, the word follows the backend while the chat stays open,
// and a lifecycle fact wins over idle only.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { mockApi } from "@/test/render";
import { HeinzelHeader } from "./HeinzelHeader";

const id = "hz-01ARZ3NDEKTSV4RRFFQ69G5FAV";
const other = "hz-01ARZ3NDEKTSV4RRFFQ69G5FAW";
const row = (session_id: string, extra: object = {}) => ({
  session_id,
  name: session_id === id ? "Helmi" : "Fritz",
  state: "awake",
  last_seq: 1,
  updated_at: "2026-11-14T09:00:00Z",
  activity: "idle",
  waiting_count: 0,
  runs_last_hour: 0,
  queued_runs: 0,
  last_run_failed: false,
  ...extra,
});

function show(busy: boolean) {
  const view = (flag: boolean) => (
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <HeinzelHeader
          sessionId={id}
          name="Helmi"
          look={{}}
          busy={flag}
          searchOpen={false}
          detailsOpen={false}
          onSearch={() => {}}
          onDetails={() => {}}
          onClose={() => {}}
        />
      </MemoryRouter>
    </QueryClientProvider>
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const result = render(view(busy));
  return { ...result, again: (flag: boolean) => result.rerender(view(flag)) };
}

afterEach(() => vi.useRealTimers());

describe("the header's state", () => {
  it("says Working with the working face while a turn runs, even when the list says Idle", async () => {
    mockApi({ "/api/heinzel/sessions": { sessions: [row(id)] } });
    const { again } = show(false);
    const face = await screen.findByRole("button", { name: "Helmi. Switch Heinzel" });
    await waitFor(() => expect(face).toHaveAccessibleDescription("Idle"));
    expect(face.querySelector("svg")).toHaveAttribute("data-state", "idle");
    again(true);
    expect(face).toHaveAccessibleDescription("Working");
    expect(face.querySelector("svg")).toHaveAttribute("data-state", "hammering");
  });

  it("keeps a stronger state while a turn runs", async () => {
    mockApi({ "/api/heinzel/sessions": { sessions: [row(id, { activity: "waiting_for_you" })] } });
    show(true);
    const face = await screen.findByRole("button", { name: "Helmi. Switch Heinzel" });
    await waitFor(() => expect(face).toHaveAccessibleDescription("Waiting for you"));
  });

  it("follows the backend while the chat stays open", async () => {
    let activity = "working";
    mockApi({ "/api/heinzel/sessions": () => ({ sessions: [row(id, { activity })] }) });
    vi.useFakeTimers({ shouldAdvanceTime: true });
    show(false);
    const face = await screen.findByRole("button", { name: "Helmi. Switch Heinzel" });
    await waitFor(() => expect(face).toHaveAccessibleDescription("Working"));
    activity = "waiting_for_you";
    await act(() => vi.advanceTimersByTimeAsync(21_000));
    await waitFor(() => expect(face).toHaveAccessibleDescription("Waiting for you"));
  });

  it("shows New for a proposal that is idle, and the activity once it has one", async () => {
    mockApi({
      "/api/heinzel/sessions": {
        sessions: [row(id), row(other, { state: "proposed" })],
      },
    });
    const user = userEvent.setup();
    show(false);
    await user.click(await screen.findByRole("button", { name: "Helmi. Switch Heinzel" }));
    const list = await screen.findByRole("dialog", { name: "Your Heinzels" });
    const fritz = within(list).getByRole("button", { name: /Fritz/ });
    expect(within(fritz).getByText("New")).toBeInTheDocument();
  });

  it("lets a working proposal show Working, and a retired one Retired", async () => {
    mockApi({
      "/api/heinzel/sessions": {
        sessions: [row(id, { state: "retired" }), row(other, { state: "proposed", activity: "working" })],
      },
    });
    const user = userEvent.setup();
    show(false);
    await user.click(await screen.findByRole("button", { name: "Helmi. Switch Heinzel" }));
    const list = await screen.findByRole("dialog", { name: "Your Heinzels" });
    expect(within(within(list).getByRole("button", { name: /Fritz/ })).getByText("Working")).toBeInTheDocument();
    expect(within(within(list).getByRole("button", { name: /Helmi/ })).getByText("Retired")).toBeInTheDocument();
  });
});
