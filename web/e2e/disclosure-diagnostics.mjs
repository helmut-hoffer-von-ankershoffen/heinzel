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
      const rect = node.getBoundingClientRect();
      return {
        ...identity(node),
        rect: [rect.x, rect.y, rect.width, rect.height, rect.top, rect.right, rect.bottom, rect.left].map(
          (value) => Math.round(value * 100) / 100,
        ),
        scrollTop: node.scrollTop,
        clientHeight: node.clientHeight,
        offsetTop: node.offsetTop,
        offsetHeight: node.offsetHeight,
        scrollHeight: node.scrollHeight,
        overflowX: style.overflowX,
        overflowY: style.overflowY,
      };
    };
    const outer = original.closest(".workspace-scroll");
    const panel = original.closest(".workspace-panel");
    const pageBlock = panel?.previousElementSibling;
    let lastScrollRequest;
    let lastRevealRequest;
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
        active: identity(document.activeElement),
        workspaceState: {
          selected: ["page", "chat", "terminal", "calendar"].includes(panel?.dataset.active)
            ? panel.dataset.active
            : "other",
          pageHeight: pageBlock?.offsetHeight,
          pageTop: pageBlock?.offsetTop,
          panelTop: panel?.offsetTop,
          outerHeight: outer?.scrollHeight,
        },
        lastScrollRequest,
        lastRevealRequest,
        ancestors: [
          ...(function* () {
            let node = current?.parentElement;
            while (node) {
              if (category(node) !== "other") yield geometry(node);
              node = node.parentElement;
            }
          })(),
        ].slice(0, 8),
        pointHits:
          point && typeof document.elementsFromPoint === "function"
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
    const changed = (event) => record(event.type, undefined, undefined, event.type === "focusin");
    const ownValue = (value, key) => {
      try {
        const descriptor = value && typeof value === "object" ? Object.getOwnPropertyDescriptor(value, key) : null;
        return descriptor && "value" in descriptor ? descriptor.value : undefined;
      } catch {
        return undefined;
      }
    };
    const previousScrollTo = outer?.scrollTo;
    const ownedScrollTo = outer && Object.hasOwn(outer, "scrollTo");
    function observeScroll(...args) {
      const options = args[0];
      const behavior = ownValue(options, "behavior");
      const top = ownValue(options, "top");
      lastScrollRequest = {
        behavior: ["auto", "instant", "smooth"].includes(behavior) ? behavior : "other",
        top: typeof top === "number" && Number.isFinite(top) ? top : null,
        before: outer.scrollTop,
      };
      record("scroll-request", undefined, undefined, true);
      const result = previousScrollTo.apply(this, args);
      record("scroll-return", undefined, undefined, true);
      return result;
    }
    if (outer && previousScrollTo) outer.scrollTo = observeScroll;
    const previousReveal = Element.prototype.scrollIntoView;
    function observeReveal(...args) {
      const observed = this instanceof Element && outer?.contains(this);
      if (observed) {
        const options = args[0];
        lastRevealRequest = {
          target: identity(this),
          argumentKind: args.length === 0 ? "default" : typeof options === "boolean" ? "legacy" : "options",
          legacyAlignment: typeof options === "boolean" ? options : null,
        };
        record("reveal-request", undefined, undefined, true);
      }
      const result = previousReveal.apply(this, args);
      if (observed) record("reveal-return", undefined, undefined, true);
      return result;
    }
    if (outer && typeof previousReveal === "function") Element.prototype.scrollIntoView = observeReveal;

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
        if (Element.prototype.scrollIntoView === observeReveal) Element.prototype.scrollIntoView = previousReveal;
        if (outer?.scrollTo === observeScroll) {
          if (ownedScrollTo) outer.scrollTo = previousScrollTo;
          else delete outer.scrollTo;
        }
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
