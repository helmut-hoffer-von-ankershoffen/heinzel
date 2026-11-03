/** Keep an interacting phone workspace in place when the page above it finishes loading. */
export function anchorWorkspace(scroller: HTMLElement, page: HTMLElement, workspace: HTMLElement): () => void {
  let height = page.offsetHeight;
  let top = workspace.offsetTop;
  let scroll = scroller.scrollTop;
  let interacting = workspace.contains(document.activeElement);
  let active = true;
  const visible = () => top < scroll + scroller.clientHeight && top + workspace.offsetHeight > scroll;
  const remember = () => {
    height = page.offsetHeight;
    top = workspace.offsetTop;
    scroll = scroller.scrollTop;
  };
  const interaction = (event: Event) => {
    interacting = event.target instanceof Node && workspace.contains(event.target);
    remember();
  };
  const moved = () => {
    // Native scroll anchoring can already have compensated. Keep the old baseline until this resize is accounted for.
    if (page.offsetHeight !== height) return;
    remember();
    if (!visible()) interacting = false;
  };
  const observer = new ResizeObserver(() => {
    if (!active) return;
    const change = page.offsetHeight - height;
    if (change && interacting && visible()) scroller.scrollTop = scroll + change;
    remember();
  });
  observer.observe(page);
  document.addEventListener("pointerdown", interaction, true);
  document.addEventListener("focusin", interaction, true);
  document.addEventListener("wheel", interaction, { capture: true, passive: true });
  scroller.addEventListener("scroll", moved);
  return () => {
    active = false;
    observer.disconnect();
    document.removeEventListener("pointerdown", interaction, true);
    document.removeEventListener("focusin", interaction, true);
    document.removeEventListener("wheel", interaction, true);
    scroller.removeEventListener("scroll", moved);
  };
}
