// Failure-only, bounded diagnostics for the media fixture. Never record URLs, tokens or response bodies.
const observed = new WeakMap();
export async function watchMedia(page) {
  const events = { attached: 0, detached: 0, navigated: 0, responses: [] };
  observed.set(page, events);
  page.on("frameattached", () => {
    events.attached += 1;
  });
  page.on("framedetached", () => {
    events.detached += 1;
  });
  page.on("framenavigated", () => {
    events.navigated += 1;
  });
  page.on("response", (response) => {
    if (events.responses.length >= 16) return;
    const url = new URL(response.url());
    const kind =
      url.pathname === "/api/system/sound/list"
        ? "sound-list"
        : url.hostname === "www.youtube-nocookie.com" && url.pathname.startsWith("/embed/")
          ? "youtube-private"
          : url.hostname === "www.youtube.com" && url.pathname.startsWith("/embed/")
            ? "youtube-account"
            : null;
    if (kind) events.responses.push({ kind, status: response.status() });
  });
  await page.addInitScript(() => {
    window.__mediaProof = { violations: [], pagehide: 0 };
    document.addEventListener("securitypolicyviolation", (event) => {
      if (window.__mediaProof.violations.length >= 8) return;
      const host = event.blockedURI.startsWith("https://www.youtube-nocookie.com/")
        ? "youtube-private"
        : event.blockedURI.startsWith("https://www.youtube.com/")
          ? "youtube-account"
          : "other";
      window.__mediaProof.violations.push({ directive: event.effectiveDirective, host });
    });
    addEventListener("pagehide", () => {
      window.__mediaProof.pagehide += 1;
    });
  });
}

export async function reportMediaFailure(page, label) {
  let evidence;
  try {
    evidence = await page.evaluate(() => ({
      diagnostics: window.__mediaProof ?? null,
      workerControlled: !!navigator.serviceWorker?.controller,
      cards: document.querySelectorAll("[data-dock]").length,
      frames: document.querySelectorAll("[data-dock] iframe").length,
      recovery: [...document.querySelectorAll("[data-dock] button")].some((node) =>
        node.textContent?.toLowerCase().includes("reload"),
      ),
    }));
  } catch {
    evidence = { documentUnavailable: true };
  }
  console.error(`${label} diagnostics ${JSON.stringify({ events: observed.get(page) ?? null, evidence })}`);
}
