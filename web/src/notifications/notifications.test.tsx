import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { SchemaNotice } from "@/api/client";
import { Providers, queryClient } from "@/app/providers";
import { shell } from "@/test/fixtures";
import { expectAccessible, mockApi } from "@/test/render";
import { appRoute, desktopState, setDesktop } from "./desktop";
import { NotificationSettings } from "./NotificationSettings";
import { follow, parseEvents } from "./stream";
import { noticePath, popUp, useNoticeStream } from "./useNoticeStream";

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
  localStorage.removeItem("hz.notification-sound");
  vi.restoreAllMocks();
});

function NoticeLocation() {
  useNoticeStream();
  const location = useLocation();
  return <output>{location.pathname + location.search}</output>;
}

describe("native system notices", () => {
  it("enables notices through the Apple app's permission flow", async () => {
    installNotification();
    const notices = vi.fn().mockResolvedValueOnce("on").mockResolvedValueOnce("off");
    vi.stubGlobal("heinzelDesktop", { notices });
    try {
      expect(await setDesktop(true)).toBe("on");
      expect(await setDesktop(false)).toBe("off");
      expect(notices.mock.calls).toEqual([[{ action: "enable" }], [{ action: "disable" }]]);
      expect(FakeNotification.requestPermission).not.toHaveBeenCalled();
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("uses the Apple app for current notices and skips expired ones", async () => {
    const notices = vi.fn().mockResolvedValueOnce(true).mockResolvedValueOnce(false);
    const registration = vi.fn(async () => null);
    vi.stubGlobal("heinzelDesktop", { notices });
    try {
      expect(await popUp(notice, vi.fn(), registration)).toBe(true);
      expect(await popUp(notice, vi.fn(), registration)).toBe(false);
      expect(await popUp({ ...notice, expires: "2000-01-01T00:00:00Z" }, vi.fn(), registration)).toBe(false);
      expect(notices).toHaveBeenCalledTimes(2);
      expect(notices).toHaveBeenCalledWith({
        action: "show",
        id: notice.id,
        title: notice.title,
        body: notice.body,
        path: noticePath(notice),
        expires: null,
      });
      expect(registration).not.toHaveBeenCalled();
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("routes a pending notification at startup and a later native tap within the workspace", async () => {
    let pending: string | null = "/contracts/example?event=first";
    const notices = vi.fn(async ({ action }) => {
      if (action !== "take") return "off";
      const route = pending;
      pending = null;
      return route;
    });
    vi.stubGlobal("heinzelDesktop", { notices });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(null, { status: 401 })),
    );
    try {
      render(
        <MemoryRouter>
          <NoticeLocation />
        </MemoryRouter>,
      );
      expect(await screen.findByText("/contracts/example?event=first")).toBeInTheDocument();
      pending = "/?panel=notifications&event=second";
      fireEvent(window, new Event("hz:native-notice"));
      expect(await screen.findByText("/?panel=notifications&event=second")).toBeInTheDocument();
      pending = "//example.com/outside";
      fireEvent(window, new Event("hz:native-notice"));
      expect(await screen.findByText("/")).toBeInTheDocument();
    } finally {
      vi.unstubAllGlobals();
    }
  });
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
  it("asks iPhone browser tabs to open from the Home Screen without requesting permission", async () => {
    installNotification();
    vi.spyOn(navigator, "userAgent", "get").mockReturnValue("Mozilla/5.0 (iPhone) AppleWebKit/605.1.15");
    mockApi(shell);
    renderSettings();
    expect(await screen.findByRole("switch", { name: "Notify me in this browser" })).toBeDisabled();
    expect(screen.getByText(/Keep hz open to receive notices/)).toBeInTheDocument();
    expect(await setDesktop(true)).toBe("install");
    expect(FakeNotification.requestPermission).not.toHaveBeenCalled();
  });

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

  it("lets the person opt into a separate new-notification ding with a user gesture", async () => {
    class GestureAudioContext {
      state: AudioContextState = "suspended";
      resume = vi.fn(async () => {
        this.state = "running";
      });
    }
    Object.defineProperty(window, "AudioContext", { configurable: true, value: GestureAudioContext });
    vi.stubGlobal("AudioContext", GestureAudioContext);
    installNotification();
    mockApi(shell);
    const user = userEvent.setup();
    const { container } = renderSettings();
    const sound = await screen.findByRole("switch", { name: "Play a sound for new notifications" });
    expect(sound).not.toBeChecked();
    expect(screen.getByText("A short ding while this app is open, apart from your music.")).toBeInTheDocument();
    await user.click(sound);
    await waitFor(() => expect(sound).toBeChecked());
    expect(localStorage.getItem("hz.notification-sound")).toBe("on");
    await expectAccessible(container);
  });

  it("say how to unblock them when the browser denied them", async () => {
    installNotification("denied");
    mockApi(shell);
    renderSettings();
    expect(await screen.findByRole("switch", { name: "Notify me in this browser" })).toBeDisabled();
    expect(screen.getByText("Blocked by your browser. Allow this page in site settings.")).toBeInTheDocument();
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
      badge: "/icons/icon-192.png",
      data: { path: `/contracts/me%3Aweekly-digest?event=${notice.id}` },
    });
  });

  it("discards expired notices after a delayed service worker registration or suspended tab", async () => {
    const showNotification = vi.fn(async () => {});
    let instant = Date.parse("2026-11-04T10:00:00Z");
    vi.spyOn(Date, "now").mockImplementation(() => instant);
    const expired = { ...notice, expires: "2026-11-04T10:00:01Z" };
    const registration = async () => {
      instant += 1000;
      return { showNotification } as unknown as ServiceWorkerRegistration;
    };
    expect(await popUp(expired, vi.fn(), registration)).toBe(false);
    showNotification.mockClear();
    expect(await popUp(expired, vi.fn(), async () => null)).toBe(false);
    expect(showNotification).not.toHaveBeenCalled();
    vi.restoreAllMocks();
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
