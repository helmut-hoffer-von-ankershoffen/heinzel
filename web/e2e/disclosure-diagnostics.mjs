// Keep pointer interaction unchanged: bounded observations go to fixture artifacts and the journey failure handler.
// Node identities distinguish a missed click from a disclosure replaced or closed after a successful click.
export async function watchDisclosure(summary, label) {
  await summary.evaluate((original, label) => {
    const key = "__hzDisclosureDiagnostics";
    const history = window[key]?.history ?? [];
    window[key]?.stop();
    const records = [];
    history.push({ label, records });
    if (history.length > 7) history.shift();
    const ids = new WeakMap();
    let nextId = 0;
    const category = (node) => {
      if (!(node instanceof Element)) return "other";
      if (node.matches(".workspace-scroll")) return "workspace-scroll";
      if (node.matches("#content")) return "page-content";
      if (node.matches(".workspace-panel")) return "workspace-panel";
      if (node.matches('[role="tabpanel"]')) return "tabpanel";
      if (node.matches(".overflow-y-auto") && node.closest('[aria-label="Chat"]')) return "durable-scroll";
      if (node.closest("footer")) return "footer";
      if (node.closest("#splash")) return "startup-overlay";
      if (node.closest(".viewport-popover")) return "popover";
      if (node.closest("#content")) return "page-descendant";
      return "other";
    };
    const identity = (node) => {
      if (!node) return null;
      if (!ids.has(node)) ids.set(node, ++nextId);
      return { id: ids.get(node), tag: node.nodeName, category: category(node) };
    };
    const geometry = (node) => {
      const style = getComputedStyle(node);
      return {
        ...identity(node),
        rect: Object.values(node.getBoundingClientRect().toJSON()).map((value) => Math.round(value * 100) / 100),
        scrollTop: node.scrollTop,
        clientHeight: node.clientHeight,
        overflowX: style.overflowX,
        overflowY: style.overflowY,
      };
    };
    let point;
    const text = original.textContent;
    const matches = () => [...document.querySelectorAll("summary")].filter((node) => node.textContent === text);
    const index = matches().indexOf(original);
    const began = performance.now();
    let previous;
    const snapshot = () => {
      const current = original.isConnected ? original : matches()[index];
      const details = current?.closest("details");
      const rect = current?.getBoundingClientRect();
      const style = current && getComputedStyle(current);
      const hit = rect && document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2);
      return {
        ancestors: [
          ...(function* () {
            let node = current?.parentElement;
            while (node) {
              if (category(node) !== "other") yield geometry(node);
              node = node.parentElement;
            }
          })(),
        ].slice(0, 8),
        pointHits: point && typeof document.elementsFromPoint === "function"
          ? document.elementsFromPoint(point.x, point.y).slice(0, 6).map(identity)
          : [],
        original: identity(original),
        originalConnected: original.isConnected,
        current: identity(current),
        details: identity(details),
        open: details?.open ?? null,
        rect: rect && [rect.x, rect.y, rect.width, rect.height].map((value) => Math.round(value * 100) / 100),
        display: style?.display,
        visibility: style?.visibility,
        pointerEvents: style?.pointerEvents,
        inert: Boolean(current?.closest("[inert]")),
        centerHit: identity(hit),
        centerHitsSummary: Boolean(current && hit && current.contains(hit)),
      };
    };
    const record = (kind, event, openChanges, force = false) => {
      const state = snapshot();
      const signature = JSON.stringify(state);
      if (!force && !event && !openChanges && signature === previous) return;
      previous = signature;
      records.push({
        ms: Math.round(performance.now() - began),
        kind,
        ...state,
        ...(openChanges && { openChanges }),
        ...(event && {
          target: identity(event.target),
          path: event.composedPath().slice(0, 6).map(identity),
          x: event.clientX,
          y: event.clientY,
          defaultPreventedAtCapture: event.defaultPrevented,
        }),
      });
      if (records.length > 64) records.shift();
    };
    const pointer = (event) => {
      point = { x: event.clientX, y: event.clientY };
      record(event.type, event);
      if (event.type === "pointerdown")
        requestAnimationFrame(() => {
          record("press-frame1", undefined, undefined, true);
          requestAnimationFrame(() => record("press-frame2", undefined, undefined, true));
        });
    };
    const changed = (event) => record(event.type);
    const pointerEvents = ["pointerdown", "pointerup", "click"];
    const stateEvents = ["toggle", "scroll", "focusin"];
    for (const type of pointerEvents) document.addEventListener(type, pointer, true);
    for (const type of stateEvents) document.addEventListener(type, changed, true);
    const observer = new MutationObserver((mutations) => {
      // An open and close in the same task has the same final snapshot; retain the ordered open-attribute evidence.
      const changes = mutations
        .filter((mutation) => mutation.attributeName === "open")
        .slice(-8)
        .map((mutation) => ({
          node: identity(mutation.target),
          wasOpen: mutation.oldValue !== null,
          openNow: mutation.target.open,
        }));
      record("mutation", undefined, changes.length ? changes : undefined);
    });
    observer.observe(document.documentElement, {
      subtree: true,
      childList: true,
      attributes: true,
      attributeOldValue: true,
      attributeFilter: ["open", "class", "style", "hidden", "inert"],
    });
    window[key] = {
      history,
      capture: () => record("failure"),
      stop: () => {
        observer.disconnect();
        for (const type of pointerEvents) document.removeEventListener(type, pointer, true);
        for (const type of stateEvents) document.removeEventListener(type, changed, true);
      },
    };
    record("armed");
  }, label);
}

export async function disclosureDiagnostics(page) {
  // Diagnostics must never replace the original failure when navigation or browser shutdown destroys the document.
  return page
    .evaluate(() => {
      const trace = window.__hzDisclosureDiagnostics;
      trace?.capture();
      return trace?.history ?? [];
    })
    .catch(() => []);
}
