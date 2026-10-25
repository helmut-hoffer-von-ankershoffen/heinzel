// The service worker (public/sw.js, ADR 0046): registered once the page has loaded, same origin, scope `/`. It shows
// notifications and keeps the app's shell; without it (an old browser, `make web-dev`) the app works all the same.
export async function registerServiceWorker(
  container: ServiceWorkerContainer | undefined = navigator.serviceWorker,
): Promise<ServiceWorkerRegistration | null> {
  if (!container || import.meta.env.DEV) return null;
  try {
    return await container.register("/sw.js", { scope: "/" });
  } catch {
    return null;
  }
}

/** The worker's registration when there is an active one, for `showNotification`; null otherwise. */
export async function workerRegistration(
  container: ServiceWorkerContainer | undefined = navigator.serviceWorker,
): Promise<ServiceWorkerRegistration | null> {
  if (!container) return null;
  try {
    return (await container.getRegistration("/")) ?? null;
  } catch {
    return null;
  }
}
