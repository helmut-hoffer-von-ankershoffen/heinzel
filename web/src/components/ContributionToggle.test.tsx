import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { SchemaOperationCall } from "@/api/client";
import { expectAccessible } from "@/test/render";
import { type ApplySettings, ContributionToggle, type ContributionToggleControl } from "./ContributionToggle";

const ORIGIN = "https://workstation.tail-example.ts.net";

function call(operation: string): SchemaOperationCall {
  return {
    method: "POST",
    path: `/example/${operation}`,
    body: {},
    query: {},
    read_only: false,
    command: `hz example ${operation}`,
    summary: `${operation} this service`,
  };
}

function control(changes: Partial<ContributionToggleControl> = {}): ContributionToggleControl {
  return {
    label: "Share this service",
    checked: false,
    requested: false,
    enable: { call: call("enable") },
    disable: { call: call("disable") },
    apply_settings: {},
    ...changes,
  };
}

function sentRequest(): Request {
  const request = vi.mocked(fetch).mock.calls[0]?.[0];
  if (!(request instanceof Request)) throw new Error("Expected an owned API request");
  return request;
}

function show(value = control()) {
  const refresh = vi.fn(async () => value);
  const applySettings = vi.fn(async () => {});
  const props = { control: value, refresh, applySettings };
  return { ...render(<ContributionToggle {...props} />), ...props };
}

beforeEach(() => {
  sessionStorage.setItem("hz.token", "session-for-tests");
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({})),
  );
});
afterEach(() => vi.restoreAllMocks());

describe("generic provider toggle", () => {
  it("one gesture sends the host-built private JSON call without query or a second confirmation", async () => {
    const user = userEvent.setup();
    const value = control({
      enable: { call: { ...call("enable"), body: { count: 3 }, query: { ignored: "private" } } },
    });
    const { refresh, applySettings } = show(value);
    await user.click(screen.getByRole("switch", { name: "Share this service" }));
    await waitFor(() => expect(refresh).toHaveBeenCalledOnce());
    expect(fetch).toHaveBeenCalledOnce();
    const request = sentRequest();
    expect(new URL(request.url).pathname).toBe("/api/example/enable");
    expect(new URL(request.url).search).toBe("");
    expect(request.headers.get("Authorization")).toBe("Bearer session-for-tests");
    expect(request.headers.get("Content-Type")).toBe("application/json");
    expect(await request.json()).toEqual({ count: 3 });
    expect(applySettings).not.toHaveBeenCalled();
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(screen.getByRole("switch")).toHaveAttribute("aria-checked", "false");
  });

  it("pending requested ON can be switched OFF while verified state remains unchecked", async () => {
    const user = userEvent.setup();
    const { refresh } = show(control({ requested: true }));
    expect(screen.getByRole("switch")).toHaveAttribute("aria-checked", "false");
    expect(screen.getByRole("status")).toHaveTextContent("Switch off to cancel");
    await user.click(screen.getByRole("switch"));
    await waitFor(() => expect(refresh).toHaveBeenCalledOnce());
    expect(new URL(sentRequest().url).pathname).toBe("/api/example/disable");
  });

  it("rereads generic status metadata before applying through the injected lifecycle controller", async () => {
    const user = userEvent.setup();
    const steps: string[] = [];
    const expected = { "hz.ui_allowed_origins": [ORIGIN] };
    vi.mocked(fetch).mockImplementation(async () => {
      steps.push("post");
      return Response.json({ unrelated: true });
    });
    const refresh = vi.fn(async () => {
      steps.push("refresh");
      return control({ requested: true, apply_settings: expected });
    });
    const applySettings = vi.fn(async (_expected: ApplySettings, signal: AbortSignal) => {
      expect(signal.aborted).toBe(false);
      steps.push("apply");
    });
    render(<ContributionToggle control={control()} refresh={refresh} applySettings={applySettings} />);
    await user.click(screen.getByRole("switch"));
    await waitFor(() => expect(steps).toEqual(["post", "refresh", "apply", "refresh"]));
    expect(applySettings).toHaveBeenCalledWith(expected, expect.any(AbortSignal));
    expect(screen.getByRole("switch")).toHaveAttribute("aria-checked", "false");
    expect(screen.queryByRole("button", { name: "Copy URL" })).not.toBeInTheDocument();
  });

  it("does not replay a refused mutation or start a restart after it", async () => {
    const user = userEvent.setup();
    vi.mocked(fetch).mockResolvedValue(
      Response.json({ error: "A conflicting owner changed the mapping." }, { status: 400 }),
    );
    const { refresh, applySettings } = show();
    await user.click(screen.getByRole("switch"));
    expect(await screen.findByRole("alert")).toHaveTextContent("A conflicting owner changed the mapping.");
    await waitFor(() => expect(refresh).toHaveBeenCalledOnce());
    expect(fetch).toHaveBeenCalledOnce();
    expect(applySettings).not.toHaveBeenCalled();
  });

  it("shows apply failure without claiming ON or retrying the mutation", async () => {
    const user = userEvent.setup();
    const refresh = vi.fn(async () => control({ requested: true, apply_settings: { "clock.zone": "Europe/Berlin" } }));
    const applySettings = vi.fn(async () => {
      throw new Error("The replacement loaded a different configuration.");
    });
    render(<ContributionToggle control={control()} refresh={refresh} applySettings={applySettings} />);
    await user.click(screen.getByRole("switch"));
    expect(await screen.findByRole("alert")).toHaveTextContent("different configuration");
    expect(fetch).toHaveBeenCalledOnce();
    expect(applySettings).toHaveBeenCalledOnce();
    expect(screen.getByRole("switch")).toHaveAttribute("aria-checked", "false");
  });

  it("suppresses repeated gestures while the first request is unresolved", async () => {
    let complete: ((response: Response) => void) | undefined;
    vi.mocked(fetch).mockImplementation(
      () =>
        new Promise((resolve) => {
          complete = resolve;
        }),
    );
    show();
    fireEvent.click(screen.getByRole("switch"));
    fireEvent.click(screen.getByRole("switch"));
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    expect(screen.getByRole("switch")).toBeDisabled();
    await act(async () => complete?.(Response.json({})));
  });

  it("aborts its pending request on unmount without starting apply or replaying the action", async () => {
    let complete: ((response: Response) => void) | undefined;
    vi.mocked(fetch).mockImplementation(
      () =>
        new Promise((resolve) => {
          complete = resolve;
        }),
    );
    const shown = show();
    fireEvent.click(screen.getByRole("switch"));
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    const request = sentRequest();
    shown.unmount();
    expect(request.signal.aborted).toBe(true);
    await act(async () => complete?.(Response.json({})));
    expect(shown.refresh).not.toHaveBeenCalled();
    expect(shown.applySettings).not.toHaveBeenCalled();
    expect(fetch).toHaveBeenCalledOnce();
  });

  it.each(["success", "uncertain"])(
    "recovers from a hung metadata refresh after %s without replaying the action",
    async (outcome) => {
      vi.useFakeTimers();
      try {
        if (outcome === "uncertain") vi.mocked(fetch).mockRejectedValue(new TypeError("The response was lost."));
        const refresh = vi.fn((_signal?: AbortSignal) => new Promise<ContributionToggleControl | null>(() => {}));
        const applySettings = vi.fn(async () => {});
        render(<ContributionToggle control={control()} refresh={refresh} applySettings={applySettings} />);
        fireEvent.click(screen.getByRole("switch"));
        await act(async () => {
          await vi.advanceTimersByTimeAsync(0);
        });
        expect(refresh).toHaveBeenCalledOnce();
        expect(screen.getByRole("switch")).toBeDisabled();
        await act(async () => {
          await vi.advanceTimersByTimeAsync(15_001);
        });
        expect(screen.getByRole("switch")).not.toBeDisabled();
        expect(refresh.mock.calls[0]?.[0]?.aborted).toBe(true);
        expect(fetch).toHaveBeenCalledOnce();
        expect(applySettings).not.toHaveBeenCalled();
      } finally {
        vi.useRealTimers();
      }
    },
  );

  it.each(["https://evil.example/action", "/../restart", "//evil.example/action"])(
    "refuses an invalid host-built action path %s",
    (path) => {
      show(control({ enable: { call: { ...call("enable"), path } } }));
      expect(screen.getByRole("switch")).toBeDisabled();
      fireEvent.click(screen.getByRole("switch"));
      expect(fetch).not.toHaveBeenCalled();
    },
  );

  it("is accessible with pending status", async () => {
    const { container } = show(control({ requested: true }));
    await expectAccessible(container);
  });
});

describe("authenticated URL copy", () => {
  it("shows and copies the usable session URL, including its fragment token", async () => {
    const user = userEvent.setup();
    const clipboard = vi.spyOn(navigator.clipboard, "writeText");
    const { container } = show(control({ checked: true, url: ORIGIN, sign_in_url: ORIGIN }));
    expect(clipboard).not.toHaveBeenCalled();
    expect(container).toHaveTextContent(`${ORIGIN}/#token=session-for-tests`);
    await user.click(screen.getByRole("button", { name: "Copy URL" }));
    expect(clipboard).toHaveBeenLastCalledWith(`${ORIGIN}/#token=session-for-tests`);
    expect(screen.queryByRole("button", { name: "Copy sign-in link" })).not.toBeInTheDocument();
    const link = screen.getByRole("link", { name: "Open" });
    expect(link).toHaveAttribute("href", `${ORIGIN}/#token=session-for-tests`);
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(fetch).not.toHaveBeenCalled();
  });

  it("shows only the public URL when the provider has not approved this host for sign-in", () => {
    const { container } = show(
      control({ checked: true, url: ORIGIN, sign_in_url: "https://other.tail-example.ts.net" }),
    );
    expect(container).toHaveTextContent(`${ORIGIN}/`);
    expect(container).not.toHaveTextContent("session-for-tests");
    expect(screen.getByRole("link", { name: "Open" })).toHaveAttribute("href", `${ORIGIN}/`);
  });

  it.each(["paired-only", "device-in-session"])("does not amplify a device credential: %s", (source) => {
    localStorage.setItem("hz.device", "hzd_device-fixture");
    if (source === "paired-only") sessionStorage.removeItem("hz.token");
    else sessionStorage.setItem("hz.token", "hzd_device-fixture");
    show(control({ checked: true, url: ORIGIN, sign_in_url: ORIGIN }));
    expect(screen.queryByRole("button", { name: "Copy sign-in link" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy URL" })).toBeInTheDocument();
  });

  it.each([null, "https://evil.example", `${ORIGIN}/path`, `${ORIGIN}?token=guess`])(
    "refuses absent or nonmatching host approval %s",
    (approved) => {
      show(control({ checked: true, url: ORIGIN, sign_in_url: approved }));
      expect(screen.queryByRole("button", { name: "Copy sign-in link" })).not.toBeInTheDocument();
    },
  );

  it("does not show a URL while readiness is unverified", () => {
    show(control({ requested: true, url: ORIGIN, sign_in_url: ORIGIN }));
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Copy/ })).not.toBeInTheDocument();
  });

  it("reports clipboard failure without showing the credential or claiming it was copied", async () => {
    const user = userEvent.setup();
    vi.spyOn(navigator.clipboard, "writeText").mockRejectedValue(new Error("session-for-tests"));
    const { container } = show(control({ checked: true, url: ORIGIN, sign_in_url: ORIGIN }));
    await user.click(screen.getByRole("button", { name: "Copy URL" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not copy the link.");
    expect(container).toHaveTextContent(`${ORIGIN}/#token=session-for-tests`);
    expect(screen.getByRole("status")).not.toHaveTextContent("Copied");
  });
});
