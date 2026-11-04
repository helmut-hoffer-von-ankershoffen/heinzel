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
  const interaction = (event: Event) => {
    interacting = event.target instanceof Node && workspace.contains(event.target) ? event.target : null;
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
    observer.disconnect();
    mutations.disconnect();
    document.removeEventListener("pointerdown", interaction, true);
    document.removeEventListener("focusin", interaction, true);
    document.removeEventListener("wheel", interaction, true);
    scroller.removeEventListener("scroll", moved);
  };
}
