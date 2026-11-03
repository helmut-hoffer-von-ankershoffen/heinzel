// Keep the pointer interaction unchanged: these bounded observations are printed only by the journey's failure handler.
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
    const identity = (node) => {
      if (!node) return null;
      if (!ids.has(node)) ids.set(node, ++nextId);
      return { id: ids.get(node), tag: node.nodeName };
    };
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
    const record = (kind, event, openChanges) => {
      const state = snapshot();
      const signature = JSON.stringify(state);
      if (!event && !openChanges && signature === previous) return;
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
    const pointer = (event) => record(event.type, event);
    const changed = (event) => record(event.type);
    const pointerEvents = ["pointerdown", "pointerup", "click"];
    const stateEvents = ["toggle", "scroll"];
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
