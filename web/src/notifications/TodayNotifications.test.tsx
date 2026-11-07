import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Providers, queryClient } from "@/App";
import type { SchemaNotice } from "@/api/client";
import { mockApi } from "@/test/render";
import { TodayNotifications } from "./TodayNotifications";

const release: SchemaNotice = {
  id: "20261107T143100000000Z-abc123",
  kind: "release",
  title: "hz 0.9.28 is available",
  body: "You have 0.9.27; `hz system changelog --newer` says what it brings.",
  route: "/doctor",
  command: "hz system update",
  key: "release:0.9.28",
  urgent: false,
  at: "2026-11-07T14:31:00Z",
  due: "2026-11-07T14:31:00Z",
  popup: true,
  held: null,
  delivered_by: null,
  delivered_at: null,
  resolve_when: { installed_version_at_least: "0.9.28" },
};

afterEach(() => {
  localStorage.removeItem("hz.notify.seen");
  vi.unstubAllGlobals();
});

describe("Today's notifications", () => {
  it("drop a notice once the server resolves it, without anyone marking it read", async () => {
    let resolved = false;
    mockApi({
      "/api/system/notify/list": () => ({ notices: resolved ? [] : [release], waiting: 0, native: "" }),
    });
    render(
      <Providers client={queryClient()}>
        <MemoryRouter>
          <ul>
            <TodayNotifications />
          </ul>
        </MemoryRouter>
      </Providers>,
    );
    expect(await screen.findByRole("link", { name: /hz 0\.9\.28 is available/ })).toBeVisible();
    expect(screen.getByRole("heading", { name: /notifications/i })).toBeVisible();

    resolved = true; // installed >= announced: the list no longer carries it
    window.dispatchEvent(new Event("hz:notice"));
    await waitFor(() => expect(screen.queryByRole("link", { name: /hz 0\.9\.28 is available/ })).toBeNull());
    expect(screen.queryByRole("heading", { name: /notifications/i })).toBeNull();
    expect(localStorage.getItem("hz.notify.seen")).toBeNull(); // read stays the person's own action
  });
});
