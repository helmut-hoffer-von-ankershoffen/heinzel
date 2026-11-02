import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Providers, queryClient } from "@/App";
import { expectAccessible, mockApi } from "@/test/render";
import { AutoUpdate, type AutoUpdateStatus, secondsLeft, updateSummary } from "./AutoUpdate";
import { updateTransport } from "./controller";
import { registerReloadHold } from "./work";

const base: AutoUpdateStatus = {
  enabled: true,
  reason: null,
  phase: "countdown",
  version: "1.0.0",
  core: "1.1.0",
  plugins: { "heinzel-example": "2.0.0" },
  seconds_left: 30,
  apply_at: "2026-11-02T09:00:30Z",
  next_window_at: null,
  postponements: 0,
  message: null,
};

function show(answers: Parameters<typeof mockApi>[0]) {
  sessionStorage.setItem("hz.token", "test-token");
  const api = mockApi(answers);
  const view = render(
    <Providers client={queryClient()}>
      <AutoUpdate />
    </Providers>,
  );
  return { ...api, ...view };
}

afterEach(() => vi.restoreAllMocks());

describe("automatic updates", () => {
  it("shows the server's countdown with what will be installed and one Postpone button", async () => {
    const { container } = show({ "/api/auto-update": base });
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent("hz updates itself in 30 s");
    expect(dialog).toHaveTextContent("Installing: hz 1.1.0, heinzel-example 2.0.0.");
    expect(screen.getAllByRole("button")).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Postpone 1 hour" })).toBeEnabled();
    await expectAccessible(container);
  });

  it("asks the server to postpone, so every window and a reload agree", async () => {
    const { calls } = show({
      "/api/auto-update": base,
      "/api/auto-update/postpone": { ...base, phase: "waiting", seconds_left: null, postponements: 1 },
    });
    fireEvent.click(await screen.findByRole("button", { name: "Postpone 1 hour" }));
    await vi.waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    const posted = calls.find((call) => call.url.endsWith("/api/auto-update/postpone"));
    expect(posted?.method).toBe("POST");
    expect(posted?.headers.get("Authorization")).toBe("Bearer test-token");
  });

  it("shows one plain message after a failure until it is read", async () => {
    const message = "The automatic update didn't work, so hz 1.0.0 keeps running as before.";
    const { calls } = show({
      "/api/auto-update": { ...base, phase: "waiting", seconds_left: null, message },
      "/api/auto-update/dismiss": { ...base, phase: "waiting", seconds_left: null, message: null },
    });
    expect(await screen.findByRole("status")).toHaveTextContent(message);
    fireEvent.click(screen.getByRole("button", { name: "OK" }));
    await vi.waitFor(() => expect(screen.queryByRole("status")).not.toBeInTheDocument());
    expect(calls.some((call) => call.url.endsWith("/api/auto-update/dismiss"))).toBe(true);
  });

  it("shows nothing when off or nothing is newer", async () => {
    const { calls } = show({ "/api/auto-update": { ...base, phase: "off", seconds_left: null } });
    await vi.waitFor(() => expect(calls.length).toBeGreaterThan(0));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("reloads once the server restarted on another version", async () => {
    const reload = vi.spyOn(updateTransport, "reload").mockImplementation(() => undefined);
    vi.spyOn(updateTransport, "clean").mockResolvedValue(undefined);
    let version = "1.0.0";
    const client = queryClient();
    sessionStorage.setItem("hz.token", "test-token");
    mockApi({ "/api/auto-update": () => ({ ...base, phase: "applying", version, seconds_left: null }) });
    render(
      <Providers client={client}>
        <AutoUpdate />
      </Providers>,
    );
    expect(await screen.findByRole("status")).toHaveTextContent("Updating hz");
    version = "1.1.0";
    await act(() => client.refetchQueries({ queryKey: ["web", "auto-update"] }));
    await vi.waitFor(() => expect(reload).toHaveBeenCalledOnce());
  });

  it("waits to reload while the composer has unsent text or a turn runs, then reloads once idle", async () => {
    const reload = vi.spyOn(updateTransport, "reload").mockImplementation(() => undefined);
    vi.spyOn(updateTransport, "clean").mockResolvedValue(undefined);
    let typing = true;
    const release = registerReloadHold(() => typing);
    let version = "1.0.0";
    const client = queryClient();
    sessionStorage.setItem("hz.token", "test-token");
    mockApi({ "/api/auto-update": () => ({ ...base, phase: "idle", version, seconds_left: null }) });
    render(
      <Providers client={client}>
        <AutoUpdate />
      </Providers>,
    );
    await vi.waitFor(() => expect(client.getQueryData(["web", "auto-update", true])).toBeDefined());
    version = "1.1.0";
    await act(() => client.refetchQueries({ queryKey: ["web", "auto-update"] }));
    expect(await screen.findByRole("status")).toHaveTextContent("Reload to finish the update.");
    await new Promise((resolve) => setTimeout(resolve, 1200));
    expect(reload).not.toHaveBeenCalled(); // the unsent text stays
    typing = false; // sent, and the answer is done
    await vi.waitFor(() => expect(reload).toHaveBeenCalledOnce(), { timeout: 3000 });
    release();
  });

  it("counts down between polls from what the server said", () => {
    expect(secondsLeft(base, 1_000, 11_500)).toBe(20);
    expect(secondsLeft(base, 1_000, 99_000)).toBe(0);
    expect(secondsLeft({ ...base, phase: "waiting" }, 0, 0)).toBeNull();
    expect(updateSummary({ ...base, core: null })).toBe("heinzel-example 2.0.0");
  });
});
