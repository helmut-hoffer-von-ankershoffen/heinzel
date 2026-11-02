// The popup follows the server even when the setting is switched off and on while a poll is on its way (ADR 0116).
import { act, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Providers, queryClient } from "@/App";
import { keys } from "@/api/queries";
import { mockApi } from "@/test/render";
import { AutoUpdate, type AutoUpdateStatus } from "./AutoUpdate";

afterEach(() => vi.restoreAllMocks());

const idle: AutoUpdateStatus = {
  enabled: true,
  reason: null,
  phase: "idle",
  version: "1.0.0",
  core: null,
  plugins: {},
  seconds_left: null,
  apply_at: null,
  next_window_at: null,
  postponements: 0,
  message: null,
};
const setting = (value: boolean) => ({
  files: [],
  plugins: [],
  settings: [{ section: "hz", key: "auto_update", value, default: true, source: "user", env: "HZ_AUTO_UPDATE" }],
});

it("shows the countdown after the setting went off and on, though a poll saw it off in between", async () => {
  let server: AutoUpdateStatus = idle;
  let config = setting(true);
  const client = queryClient();
  sessionStorage.setItem("hz.token", "test-token");
  mockApi({ "/api/auto-update": () => server, "/api/system/config/show": () => config });
  render(
    <Providers client={client}>
      <AutoUpdate />
    </Providers>,
  );
  await vi.waitFor(() => expect(client.getQueryData(keys.config)).toBeDefined());
  // Turned off: the server says so at once; this window's poll sees it before its own copy of the setting changes.
  server = { ...idle, phase: "off", enabled: false, reason: "Automatic updates are off." };
  await new Promise((resolve) => setTimeout(resolve, 2300));
  config = setting(false);
  await act(() => client.invalidateQueries({ queryKey: keys.config }));
  // Turned on again, and a release is announced: the countdown must show within a poll or two.
  server = { ...idle, phase: "countdown", core: "9.9.9", seconds_left: 30, apply_at: "2026-11-02T09:00:30Z" };
  config = setting(true);
  await act(() => client.invalidateQueries({ queryKey: keys.config }));
  expect(await screen.findByRole("alertdialog", {}, { timeout: 5000 })).toHaveTextContent("hz updates itself in");
}, 15000);
