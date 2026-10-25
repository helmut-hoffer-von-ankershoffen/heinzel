// While the app is open: follow the notice stream, tell the app's notification center, and pop up due notices when
// allowed (ADR 0043). Every notice that arrives is dispatched as a window event `hz:notice` (its JSON as `detail`), and
// a change of the list as `hz:notices`, so any screen can refresh without its own stream.
// Notifications go through the service worker (`showNotification`), so they work in the installed app and a click
// focuses or opens the app at the notice; a notice this tab popped up is acknowledged, so the daemon never shows it
// again. A tab that may not pop up claims nothing, so the daemon's native notification still reaches the person.
import { useEffect } from "react";
import { type NavigateFunction, useNavigate } from "react-router";
import { api, type SchemaNotice } from "@/api/client";
import { workerRegistration } from "@/pwa/register";
import { appRoute, useDesktopState } from "./desktop";
import { follow } from "./stream";

/** The notice's screen with its id, so the app opens on it (`?event=<id>`). */
export function noticePath(notice: Pick<SchemaNotice, "id" | "route">): string {
  const route = appRoute(notice.route);
  return `${route}${route.includes("?") ? "&" : "?"}event=${encodeURIComponent(notice.id)}`;
}

/** Pop up a notification through the service worker, else from the page; whether it was shown. */
export async function popUp(
  notice: Pick<SchemaNotice, "id" | "title" | "body" | "route">,
  navigate: NavigateFunction,
  registration: () => Promise<ServiceWorkerRegistration | null> = workerRegistration,
): Promise<boolean> {
  const path = noticePath(notice);
  const options = {
    body: notice.body,
    tag: notice.id,
    icon: "/icons/icon-192.png",
    badge: "/icons/icon-192.png",
    data: { path },
  };
  try {
    const worker = await registration();
    if (worker) {
      await worker.showNotification(notice.title, options); // its click is the worker's (public/sw.js)
      return true;
    }
    const shown = new Notification(notice.title, options);
    shown.onclick = () => {
      window.focus();
      navigate(path);
      shown.close();
    };
    return true;
  } catch {
    return false; // not allowed after all: the daemon shows it then
  }
}

export function useNoticeStream() {
  const navigate = useNavigate();
  const desktop = useDesktopState();
  useEffect(() => {
    // A click on a notification the worker showed, while this window is open: go to the notice here.
    const worker = navigator.serviceWorker;
    const onMessage = (event: MessageEvent) => {
      const data = event.data as { type?: string; path?: string } | null;
      if (data?.type === "hz:navigate" && typeof data.path === "string") navigate(appRoute(data.path));
    };
    worker?.addEventListener("message", onMessage);
    return () => worker?.removeEventListener("message", onMessage);
  }, [navigate]);
  useEffect(() => {
    const controller = new AbortController();
    void follow(
      (event) => {
        if (event.event === "changed") {
          window.dispatchEvent(new CustomEvent("hz:notices", { detail: { latest: event.latest } }));
          return;
        }
        window.dispatchEvent(new CustomEvent("hz:notice", { detail: event.notice }));
        void popUp(event.notice, navigate).then((shown) => {
          if (shown) void api.POST("/system/notify/ack", { params: { query: { ids: event.notice.id } } });
        });
      },
      { popups: desktop === "on", signal: controller.signal },
    );
    return () => controller.abort();
  }, [desktop, navigate]);
}
