/** Keep an interacting phone workspace in place when the page above it finishes loading. */
export function anchorWorkspace(scroller: HTMLElement, page: HTMLElement, workspace: HTMLElement): () => void {
  let height = page.offsetHeight;
  let top = workspace.offsetTop;
  let scroll = scroller.scrollTop;
  let viewportHeight = scroller.clientHeight;
  let viewportWidth = scroller.clientWidth;
  let interacting: Node | null = workspace.contains(document.activeElement) ? document.activeElement : null;
  let active = true;
  const visible = () =>
    !workspace.hidden &&
    workspace.offsetHeight > 0 &&
    top < scroll + scroller.clientHeight &&
    top + workspace.offsetHeight > scroll;
  const remember = () => {
    height = page.offsetHeight;
    top = workspace.offsetTop;
    scroll = scroller.scrollTop;
    viewportHeight = scroller.clientHeight;
    viewportWidth = scroller.clientWidth;
  };
  let cancelAcknowledgment = () => {};
  const stopReveal = (event: Event) => {
    cancelAcknowledgment();
    const pointer = event as PointerEvent;
    const target = event.target;
    if (
      event.type !== "pointerdown" ||
      !interacting ||
      !(target instanceof Element) ||
      !scroller.scrollTo ||
      scroller.scrollHeight <= scroller.clientHeight ||
      (pointer.button !== undefined && pointer.button !== 0) ||
      pointer.isPrimary === false ||
      target.closest('input, textarea, select, label, [contenteditable]:not([contenteditable="false"])')
    )
      return;
    const control = target.closest("button, summary, a[href], [tabindex]") ?? target;
    const rect = control.getBoundingClientRect();
    const bounds = scroller.getBoundingClientRect();
    const visual = window.visualViewport;
    // A clipped control may legitimately reveal itself when native focus follows this press.
    // Leave that reveal entirely to the browser rather than restoring over it next frame.
    if (
      rect.left < Math.max(bounds.left + scroller.clientLeft, visual?.offsetLeft ?? 0) ||
      rect.top < Math.max(bounds.top + scroller.clientTop, visual?.offsetTop ?? 0) ||
      rect.right >
        Math.min(
          bounds.left + scroller.clientLeft + scroller.clientWidth,
          (visual?.offsetLeft ?? 0) + (visual?.width ?? window.innerWidth),
        ) ||
      rect.bottom >
        Math.min(
          bounds.top + scroller.clientTop + scroller.clientHeight,
          (visual?.offsetTop ?? 0) + (visual?.height ?? window.innerHeight),
        )
    )
      return;
    const position = () => {
      const rect = control.getBoundingClientRect();
      const visual = window.visualViewport;
      return [
        page.offsetHeight,
        workspace.offsetTop,
        workspace.offsetHeight,
        scroller.clientWidth,
        scroller.clientHeight,
        scroller.scrollHeight,
        rect.top + scroller.scrollTop,
        rect.left + scroller.scrollLeft,
        rect.width,
        rect.height,
        visual?.width,
        visual?.height,
        visual?.offsetTop,
        visual?.offsetLeft,
      ];
    };
    const before = position();
    const current = scroller.scrollTop;
    const left = scroller.scrollLeft;
    const navigation = [
      "wheel",
      "keydown",
      "pointermove",
      "touchmove",
      "pointerup",
      "pointercancel",
      "touchend",
      "touchcancel",
    ];
    const focus = (next: Event) => {
      if (next.target !== control) cleanup();
    };
    const cleanup = () => {
      cancelAnimationFrame(frame);
      for (const name of navigation) document.removeEventListener(name, cleanup, true);
      document.removeEventListener("focusin", focus, true);
      window.removeEventListener("resize", cleanup);
      window.visualViewport?.removeEventListener("resize", cleanup);
      window.visualViewport?.removeEventListener("scroll", cleanup);
      cancelAcknowledgment = () => {};
    };
    // Native compositor deltas can arrive after the synchronous abort. Acknowledge just that
    // already-in-flight frame, before paint; never hold the position across user navigation.
    scroller.scrollTo({ top: current, left, behavior: "instant" });
    const frame = requestAnimationFrame(() => {
      cleanup();
      if (
        active &&
        scroller.isConnected &&
        page.isConnected &&
        scroller.contains(workspace) &&
        control.isConnected &&
        workspace.contains(control) &&
        !workspace.hidden &&
        position().every((value, index) => value === before[index])
      )
        scroller.scrollTo({ top: current, left, behavior: "instant" });
    });
    cancelAcknowledgment = cleanup;
    for (const name of navigation) document.addEventListener(name, cleanup, { capture: true, passive: true });
    document.addEventListener("focusin", focus, true);
    window.addEventListener("resize", cleanup);
    window.visualViewport?.addEventListener("resize", cleanup);
    window.visualViewport?.addEventListener("scroll", cleanup);
  };
  const interaction = (event: Event) => {
    interacting = event.target instanceof Node && workspace.contains(event.target) ? event.target : null;
    if (event.type === "pointerdown") stopReveal(event);
    remember();
  };
  const moved = () => {
    // Native scroll anchoring can already have compensated. Keep the old baseline until this resize is accounted for.
    if (page.offsetHeight !== height) return;
    remember();
    if (!visible()) interacting = null;
  };
  const reconcile = () => {
    if (!active || !page.isConnected || !workspace.isConnected || !scroller.contains(workspace)) return;
    const change = page.offsetHeight - height;
    // Viewport reflow belongs to the browser and focused-editor handler, not content anchoring.
    const reflow = scroller.clientHeight !== viewportHeight || scroller.clientWidth !== viewportWidth;
    if (change && !reflow && interacting?.isConnected && workspace.contains(interacting) && visible())
      scroller.scrollTop = scroll + change;
    remember();
  };
  // A DOM update can move a pressed control before ResizeObserver's next rendering notification. Reconcile
  // its layout at the mutation microtask checkpoint, before another pointer event can hit the new position.
  const mutations = new MutationObserver(reconcile);
  mutations.observe(page, {
    subtree: true,
    childList: true,
    characterData: true,
    attributes: true,
    attributeFilter: ["class", "style", "hidden"],
  });
  const observer = new ResizeObserver(reconcile);
  observer.observe(page);
  document.addEventListener("pointerdown", interaction, true);
  document.addEventListener("focusin", interaction, true);
  document.addEventListener("wheel", interaction, { capture: true, passive: true });
  scroller.addEventListener("scroll", moved);
  return () => {
    active = false;
    cancelAcknowledgment();
    observer.disconnect();
    mutations.disconnect();
    document.removeEventListener("pointerdown", interaction, true);
    document.removeEventListener("focusin", interaction, true);
    document.removeEventListener("wheel", interaction, true);
    scroller.removeEventListener("scroll", moved);
  };
}
