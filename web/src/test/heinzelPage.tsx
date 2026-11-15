// Made-up data and one render for the tests of the Heinzel's page and its sections.
import { render } from "@testing-library/react";
import type { ReactNode } from "react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { Providers, queryClient } from "@/App";
import type { components } from "@/api/schema";

export const sessionId = "hz-01ARZ3NDEKTSV4RRFFQ69G5FAV";

export const schedule: components["schemas"]["WakeScheduleView"] = {
  summary: "every weekday at 08:00",
  cron: "0 8 * * 1-5",
  timezone: "Europe/Berlin",
  status: "active",
  contract_version: 1,
  revision: 3,
  preview_hash: "c".repeat(64),
  next_wake: "2026-11-16T07:00:00Z",
  last_run: null,
};

export const snapshot = {
  session_id: sessionId,
  heinzel_id: sessionId,
  kind: "heinzel",
  name: "Helmi",
  appearance: { age: "grown", build: "regular", accessories: [] },
  profile_revision: 1,
  contract_version: null,
  state: "awake",
  last_seq: 1,
  messages: [],
  pending_actions: [],
  questions: [],
  contracts: [],
  attachments: [],
  costs: { spent_usd: 0, session_max_usd: 2, message_max_usd: 0.2, unknown_turns: 0 },
  schedule,
  has_more: false,
} as unknown as components["schemas"]["SessionSnapshot"];

/** Render inside the app's providers and a router at `path`, holding `element` at `/heinzel/:id`. */
export function renderPage(element: ReactNode, path = `/heinzel/${sessionId}`) {
  sessionStorage.setItem("hz.token", "test-token");
  const router = createMemoryRouter([{ path: "/heinzel/:id", element }], { initialEntries: [path] });
  const view = render(
    <Providers client={queryClient()}>
      <RouterProvider router={router} />
    </Providers>,
  );
  return { router, ...view };
}

export const posted = (calls: Request[], suffix: string) =>
  calls.filter((request) => request.method === "POST" && new URL(request.url).pathname.endsWith(suffix));
