import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, expect, it } from "vitest";
import { Providers, queryClient, routes } from "@/App";
import type { ViewEntry } from "@/plugins/Views";
import { base } from "@/test/chat";
import { mockApi } from "@/test/render";

const originalWidth = window.innerWidth;
const session = "hz-01ARZ3NDEKTSV4RRFFQ69G5FAV";
const contributed: ViewEntry = {
  id: "fixture:status",
  title: "Status view",
  fallback: "Fixture view unavailable",
  slot: "panel",
  order: 1,
  refresh: 0,
  abi: 1,
  section: null,
  route: null,
  bundle: null,
  styles: [],
  actions: {},
  source: null,
  problem: "The fixture deliberately supplies no executable bundle.",
};

afterEach(() => {
  Object.defineProperty(window, "innerWidth", { configurable: true, value: originalWidth });
  localStorage.clear();
  sessionStorage.clear();
});

function mountedRouter(path: string, views: ViewEntry[] = []) {
  Object.defineProperty(window, "innerWidth", { configurable: true, value: 390 });
  sessionStorage.setItem("hz.token", "test-token");
  mockApi({
    ...base,
    "/api/system/ui/views": views,
    "/api/heinzel/list": { heinzelmaennchen: [], next: [] },
    "/api/heinzel/sessions": { sessions: [] },
    "/api/heinzel/session": { session_id: session, heinzel_id: session, name: "Personal helper", messages: [] },
  });
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  render(
    <Providers client={queryClient()}>
      <RouterProvider router={router} />
    </Providers>,
  );
  return router;
}

it("keeps a newly selected personal conversation when Chat is selected before its route render commits", async () => {
  const router = mountedRouter("/heinzel?keep=present");
  const chat = await screen.findByRole("tab", { name: "Chat" });
  await act(async () => {
    const navigation = router.navigate(`/heinzel?chat=${session}&keep=present`);
    fireEvent.click(chat);
    await navigation;
  });
  expect(router.state.location.pathname).toBe("/heinzel");
  const query = new URLSearchParams(router.state.location.search);
  expect(query.get("keep")).toBe("present");
  expect(query.get("chat")).toBe(session);
});

it.each(["Chat", "Page", "Terminal"])(
  "preserves conversation and other query keys when %s replaces a contributed panel",
  async (native) => {
    const router = mountedRouter(`/heinzel?chat=${session}&keep=present&panel=${contributed.id}`, [contributed]);
    const status = await screen.findByRole("tab", { name: contributed.title });
    await waitFor(() => expect(status).toHaveAttribute("aria-selected", "true"));
    const user = userEvent.setup();
    if (native === "Terminal") await user.click(screen.getByRole("button", { name: "Open a terminal" }));
    else await user.click(screen.getByRole("tab", { name: native }));
    await waitFor(() => expect(new URLSearchParams(router.state.location.search).get("panel")).toBeNull());
    expect(new URLSearchParams(router.state.location.search).get("chat")).toBe(session);
    expect(new URLSearchParams(router.state.location.search).get("keep")).toBe("present");
    await user.click(status);
    await waitFor(() => expect(new URLSearchParams(router.state.location.search).get("panel")).toBe(contributed.id));
    expect(new URLSearchParams(router.state.location.search).get("chat")).toBe(session);
    expect(new URLSearchParams(router.state.location.search).get("keep")).toBe("present");
  },
);
