// Observe the native activation without intercepting events or changing selection or scroll.
export async function watchWorkshopReload(page) {
  return page.evaluate(installWorkshopReloadProbe).catch(() => false);
}

export async function readWorkshopReload(page) {
  return page.evaluate(() => window.__hzWorkshopReload?.() ?? null).catch(() => null);
}

function installWorkshopReloadProbe() {
  const workspace = document.querySelector(".workspace-panel");
  if (!workspace) return false;
  const outer = workspace.closest(".workspace-scroll");
  const pageBlock = workspace.previousElementSibling;
  const chatTab = () => workspace.querySelector('[role="tab"][aria-controls$="-chat"]');
  const chatPanel = (tab) => {
    const controlled = tab?.getAttribute("aria-controls");
    return controlled ? document.getElementById(controlled) : null;
  };
  const started = performance.now();
  const records = [];
  const cap = 24;
  const events = ["pointerdown", "pointerup", "pointercancel", "click"];
  const bounded = (value) => (Number.isFinite(value) ? Math.max(-16384, Math.min(16384, Math.round(value))) : null);
  const activePanel = (value) => (value === "page" || value === "chat" ? value : "other");
  const category = (target, tab) => {
    if (!(target instanceof Element)) return "other";
    if (tab?.contains(target)) return "chat-tab";
    if (target.closest('[role="tab"]')) return "other-tab";
    if (target.closest("#splash")) return "startup";
    if (target.closest('[role="dialog"]')) return "dialog";
    if (pageBlock?.contains(target)) return "page";
    if (workspace.contains(target)) return "workspace";
    return "other";
  };
  const snapshot = (kind, event) => {
    const tab = chatTab();
    const box = tab?.getBoundingClientRect();
    return {
      kind,
      ms: Math.min(30000, Math.round(performance.now() - started)),
      trusted: event?.isTrusted ?? null,
      hit: event ? category(event.target, tab) : null,
      active: activePanel(workspace.dataset.active),
      selected: tab ? tab.getAttribute("aria-selected") === "true" : null,
      panelHidden: chatPanel(tab)?.hidden ?? null,
      rootInert: document.getElementById("root")?.hasAttribute("inert") ?? null,
      pageHeight: bounded(pageBlock?.offsetHeight),
      workspaceTop: bounded(workspace.offsetTop),
      scrollTop: bounded(outer?.scrollTop),
      tabBox: box ? [box.x, box.y, box.width, box.height].map(bounded) : null,
    };
  };
  const record = (kind, event) => {
    if (records.length < cap) records.push(snapshot(kind, event));
  };
  const observeEvent = (event) => record(event.type, event);
  const selection = new MutationObserver((changes) => {
    for (const { attributeName, oldValue } of changes) {
      if (records.length >= cap) break;
      const state = snapshot("selection");
      if (attributeName === "data-active") state.previousActive = activePanel(oldValue);
      else if (attributeName === "aria-selected") state.previousSelected = oldValue === "true";
      else state.previousHidden = oldValue !== null;
      records.push(state);
    }
  });
  const watch = (node, name) => {
    if (node) selection.observe(node, { attributeOldValue: true, attributeFilter: [name] });
  };
  const tab = chatTab();
  watch(workspace, "data-active");
  watch(tab, "aria-selected");
  watch(chatPanel(tab), "hidden");
  for (const type of events) window.addEventListener(type, observeEvent, { capture: true, passive: true });
  record("before");
  window.__hzWorkshopReload = () => {
    selection.disconnect();
    for (const type of events) window.removeEventListener(type, observeEvent, true);
    delete window.__hzWorkshopReload;
    return [...records, snapshot("failure")];
  };
  return true;
}
