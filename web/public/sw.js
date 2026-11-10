// The web app's service worker (ADR 0046): it shows notifications, opens the app at a notice when one is clicked, and
// answers with a small offline page that reloads itself once hz is back when `hz ui serve` isn't running. It never caches API data and never talks to another origin.
// BUILD is stamped by the web build (src/pwa/shellBuild.ts) with a hash of the built app, so every build is a new
// worker: the browser installs it, it keeps the shell files of that build under a cache of its own name and drops
// every other build's cache. A constant name kept one build's offline page and retry script for good: the worker's
// bytes did not change, so no later build ever installed. `make web-dev` serves this file unstamped.
const BUILD = "dev";
const SHELL = `hz-shell-${BUILD}`;
const OFFLINE = "/offline.html";
const PRECACHED = ["/manifest.webmanifest", "/favicon.svg", "/icons/icon-192.png", "/boot-retry.js"];

/** One structured event (ADR 0138 names and fields) on the worker's console; logging never fails the worker. */
function log(event, fields, level = "info") {
  try {
    console[level](JSON.stringify({ event, version: BUILD, ...fields }));
  } catch {
    // no console: nothing to tell
  }
}

/** A step of the worker's life: a start event, then one outcome event with its duration; a failure is re-thrown. */
function step(event, work) {
  const started = Date.now();
  log(event, { operation: "web.shell", phase: "start" });
  return work().then(
    (fields) => {
      log(event, { operation: "web.shell", phase: "end", outcome: "ok", duration_ms: Date.now() - started, ...fields });
    },
    (error) => {
      log(
        event,
        {
          operation: "web.shell",
          phase: "end",
          outcome: "error",
          duration_ms: Date.now() - started,
          error_type: error?.name ?? typeof error,
          error: String(error?.message ?? error),
        },
        "error",
      );
      throw error;
    },
  );
}

self.addEventListener("install", (event) => {
  // Past the HTTP cache (`reload`): a copy the browser kept from an older build is not this build's file.
  const fresh = [OFFLINE, ...PRECACHED].map(
    (path) => new Request(new URL(path, self.location.origin), { cache: "reload" }),
  );
  event.waitUntil(
    step("web.shell.install", () =>
      caches
        .open(SHELL)
        .then((cache) => cache.addAll(fresh))
        .then(() => ({ step: "precache", count: fresh.length })),
    ).then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    step("web.shell.activate", () =>
      caches.keys().then((names) => {
        const stale = names.filter((name) => name.startsWith("hz-shell-") && name !== SHELL);
        return Promise.all(stale.map((name) => caches.delete(name))).then(() => ({
          step: "drop-stale",
          count: stale.length,
        }));
      }),
    ).then(() => self.clients.claim()),
  );
});

/** This build's copy only: `caches.match` searches every cache, and an older build's may still be there. */
function kept(...lookup) {
  return caches.open(SHELL).then((cache) => cache.match(...lookup));
}

/** The answer to a page request that hz did not answer: this build's offline page, logged with why. */
async function offlinePage(error) {
  log(
    "web.shell.offline",
    { operation: "web.shell", step: "navigate", outcome: "skipped", reason: "network", error_type: error?.name },
    "warn",
  );
  return (await kept(OFFLINE)) || Response.error();
}

// Keeping a copy is best effort: a full or unavailable cache must never fail the response being served.
function remember(request, response) {
  return caches
    .open(SHELL)
    .then((cache) => cache.put(request, response))
    .catch(() => undefined);
}

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== self.location.origin || url.pathname.startsWith("/api/")) {
    return; // the network, always: API data is never cached
  }
  if (url.searchParams.has("hz-probe")) {
    return; // the offline page asking whether hz is back: the network's answer, never a cached copy
  }
  if (event.request.mode === "navigate") {
    // The page from the network, so it is always current. When hz isn't running, the offline page: it reloads itself
    // once hz answers. The app's old page is not kept: it could not start without hz, and went stale (an old version,
    // a missing icon, old guidance).
    event.respondWith(fetch(event.request).catch(offlinePage));
    return;
  }
  if (PRECACHED.includes(url.pathname)) {
    // The installed shell's own files: current from the network, the cached copy (whatever its `?v=`) when hz is down.
    event.respondWith(fetch(event.request).catch(() => kept(event.request, { ignoreSearch: true })));
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
              event.waitUntil(remember(event.request, copy));
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
