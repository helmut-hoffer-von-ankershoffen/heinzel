import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Providers, queryClient } from "@/App";
import type { SchemaNotice } from "@/api/client";
import { shell } from "@/test/fixtures";
import { expectAccessible, mockApi } from "@/test/render";
import { appRoute, desktopState, setDesktop } from "./desktop";
import { NotificationSettings } from "./NotificationSettings";
import { follow, parseEvents } from "./stream";
import { noticePath, popUp } from "./useNoticeStream";

const notice: SchemaNotice = {
  id: "20261021T100000000000Z-abc123",
  kind: "draft",
  title: "Draft ready: Weekly digest",
  body: "Read it, edit what is wrong, then send it.",
  route: "/contracts/me%3Aweekly-digest",
  command: "hz heinzel drafts --name me:weekly-digest --latest",
  key: "draft:me:weekly-digest:sha256:1",
  urgent: false,
  at: "2026-10-21T10:00:00Z",
  due: "2026-10-21T10:00:00Z",
  popup: true,
  held: null,
  delivered_by: null,
  delivered_at: null,
};

/** A stand-in for the browser's Notification: records what was shown and what was asked. */
class FakeNotification {
  static permission: NotificationPermission = "default";
  static shown: FakeNotification[] = [];
  static requestPermission = vi.fn(async () => {
    FakeNotification.permission = "granted";
    return "granted" as NotificationPermission;
  });
  onclick: (() => void) | null = null;
  close = vi.fn();
  constructor(
    readonly title: string,
    readonly options: NotificationOptions,
  ) {
    FakeNotification.shown.push(this);
  }
}

function installNotification(permission: NotificationPermission = "default") {
  FakeNotification.permission = permission;
  FakeNotification.shown = [];
  FakeNotification.requestPermission.mockClear();
  vi.stubGlobal("Notification", FakeNotification);
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("the notice stream", () => {
  it("parses events across chunks and skips comments and damage", () => {
    const first = parseEvents(`retry: 3000\n\n: ping\n\nevent: notice\ndata: ${JSON.stringify(notice)}\n\nevent: chan`);
    expect(first.events).toEqual([{ event: "notice", notice }]);
    const second = parseEvents(`${first.rest}ged\ndata: {"latest": "x"}\n\nevent: notice\ndata: {broken\n\n`);
    expect(second.events).toEqual([{ event: "changed", latest: "x" }]);
    expect(second.rest).toBe("");
  });

  it("reads the stream with the Bearer header, never with the token in the URL", async () => {
    sessionStorage.setItem("hz.token", "test-token");
    const requests: Request[] = [];
    const controller = new AbortController();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (request: Request) => {
        requests.push(request);
        return new Response(`event: changed\ndata: {"latest": "a"}\n\n`, { status: 200 });
      }),
    );
    const seen: unknown[] = [];
    await follow((event) => seen.push(event), {
      popups: true,
      signal: controller.signal,
      wait: async () => controller.abort(),
    });
    expect(seen).toEqual([{ event: "changed", latest: "a" }]);
    expect(requests[0]?.url).toBe(`${window.location.origin}/api/system/notify/stream?popups=true`);
    expect(requests[0]?.url).not.toContain("test-token");
    expect(requests[0]?.headers.get("Authorization")).toBe("Bearer test-token");
  });

  it("stops on a refused token instead of retrying", async () => {
    const fetch = vi.fn(async () => new Response("{}", { status: 401 }));
    vi.stubGlobal("fetch", fetch);
    await follow(() => {}, { popups: false, signal: new AbortController().signal, wait: async () => {} });
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});

function renderSettings() {
  sessionStorage.setItem("hz.token", "test-token");
  return render(
    <Providers client={queryClient()}>
      <MemoryRouter>
        <NotificationSettings />
      </MemoryRouter>
    </Providers>,
  );
}

describe("desktop notifications", () => {
  it("ask for permission only when turned on, never on page load", async () => {
    installNotification();
    mockApi(shell);
    const user = userEvent.setup();
    const { container } = renderSettings();
    const toggle = await screen.findByRole("switch", { name: "Notify me in this browser" });
    expect(FakeNotification.requestPermission).not.toHaveBeenCalled();
    expect(toggle).not.toBeChecked();
    await user.click(toggle);
    expect(FakeNotification.requestPermission).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(toggle).toBeChecked());
    expect(desktopState()).toBe("on");
    await expectAccessible(container);
    await user.click(toggle);
    expect(desktopState()).toBe("off");
  });

  it("say how to unblock them when the browser denied them", async () => {
    installNotification("denied");
    mockApi(shell);
    renderSettings();
    expect(await screen.findByRole("switch", { name: "Notify me in this browser" })).toBeDisabled();
    expect(screen.getByText(/blocks notifications from this page/)).toBeInTheDocument();
  });

  it("are unsupported without the Notification API", async () => {
    expect(desktopState()).toBe("unsupported");
    expect(await setDesktop(true)).toBe("unsupported");
  });

  it("go through the service worker, which opens the notice when clicked", async () => {
    const showNotification = vi.fn(async () => {});
    const registration = async () => ({ showNotification }) as unknown as ServiceWorkerRegistration;
    expect(await popUp(notice, vi.fn(), registration)).toBe(true);
    expect(showNotification).toHaveBeenCalledWith(notice.title, {
      body: notice.body,
      tag: notice.id,
      icon: "/icons/icon-192.png",
      data: { path: `/contracts/me%3Aweekly-digest?event=${notice.id}` },
    });
  });

  it("without a service worker, focus the tab and open the notice's screen when clicked", async () => {
    installNotification("granted");
    const navigate = vi.fn();
    const focus = vi.spyOn(window, "focus").mockImplementation(() => {});
    expect(await popUp(notice, navigate, async () => null)).toBe(true);
    const shown = FakeNotification.shown[0];
    expect(shown?.title).toBe("Draft ready: Weekly digest");
    expect(shown?.options.tag).toBe(notice.id);
    shown?.onclick?.();
    expect(focus).toHaveBeenCalled();
    expect(navigate).toHaveBeenCalledWith(`/contracts/me%3Aweekly-digest?event=${notice.id}`);
    expect(shown?.close).toHaveBeenCalled();
  });

  it("never route to another origin", () => {
    expect(appRoute("/doctor")).toBe("/doctor");
    for (const route of [
      "//evil.example.test/x",
      "https://evil.example.test",
      "/\\evil",
      "/\t/evil.example.test",
      "javascript:alert(1)",
    ]) {
      expect(appRoute(route)).toBe("/");
    }
    expect(noticePath({ id: "a b", route: "https://evil.example.test" })).toBe("/?event=a%20b");
  });
});
