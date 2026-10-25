// The web app's service worker (ADR 0046): it shows notifications, opens the app at a notice when one is clicked, and
// keeps the app's shell for when `hz ui` isn't running. It never caches API data and never talks to another origin.
const SHELL = "hz-shell-v3";
const PRECACHED = ["/manifest.webmanifest", "/favicon.svg", "/icons/icon-192.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(SHELL)
      .then((cache) => cache.addAll(["/", ...PRECACHED]))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((names) =>
        Promise.all(
          names.filter((name) => name.startsWith("hz-shell-") && name !== SHELL).map((name) => caches.delete(name)),
        ),
      )
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== self.location.origin || url.pathname.startsWith("/api/")) {
    return; // the network, always: API data is never cached
  }
  if (event.request.mode === "navigate") {
    // The page from the network, so it is always current; the shell when `hz ui` isn't running.
    event.respondWith(
      fetch(event.request)
        .then((response) => {
          if (response.ok && response.type === "basic") {
            const copy = response.clone();
            caches.open(SHELL).then((cache) => cache.put("/", copy));
          }
          return response;
        })
        .catch(() => caches.match("/")),
    );
    return;
  }
  if (PRECACHED.includes(url.pathname)) {
    // The installed shell's own files: current from the network, the cached copy when `hz ui` isn't running.
    event.respondWith(fetch(event.request).catch(() => caches.match(event.request)));
    return;
  }
  if (url.pathname.startsWith("/assets/")) {
    // Hashed file names: a cached copy is the same file.
    event.respondWith(
      caches.match(event.request).then(
        (cached) =>
          cached ||
          fetch(event.request).then((response) => {
            if (response.ok && response.type === "basic") {
              const copy = response.clone();
              caches.open(SHELL).then((cache) => cache.put(event.request, copy));
            }
            return response;
          }),
      ),
    );
  }
});

/** A path of this app, or `/`: resolved first, then compared, so no spelling can leave this origin. */
function appPath(path) {
  if (typeof path !== "string" || !path.startsWith("/")) return "/";
  try {
    const url = new URL(path, self.location.origin);
    return url.origin === self.location.origin ? `${url.pathname}${url.search}${url.hash}` : "/";
  } catch {
    return "/";
  }
}

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = new URL(appPath(event.notification.data?.path), self.location.origin).href;
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((windows) => {
      const open = windows.find((client) => new URL(client.url).origin === self.location.origin);
      if (open) {
        open.postMessage({ type: "hz:navigate", path: appPath(event.notification.data?.path) });
        return open.focus();
      }
      return self.clients.openWindow(target);
    }),
  );
});
