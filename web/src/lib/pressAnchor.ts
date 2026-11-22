/**
 * Keep a pressed page control under the pointer when content above it changes size during the press.
 *
 * Native scroll anchoring does this in Chromium, but WebKit's anchoring picks its own anchor node and does not always
 * hold the pressed control, so a late page answer can move a button between press and release and the click lands on
 * whatever slid under the pointer. The press is measured itself, so a browser that already anchored leaves no shift.
 */
export function anchorPress(scroller: HTMLElement, region: HTMLElement): () => void {
  let press: { control: Element; top: number; width: number; height: number } | null = null;
  const measure = (control: Element) => ({
    control,
    top: control.getBoundingClientRect().top,
    width: scroller.clientWidth,
    height: scroller.clientHeight,
  });
  const start = (event: Event) => {
    press = null;
    const pointer = event as PointerEvent;
    const target = event.target;
    if (
      !(target instanceof Element) ||
      !region.contains(target) ||
      (pointer.button !== undefined && pointer.button !== 0) ||
      pointer.isPrimary === false
    )
      return;
    const control = target.closest('button, summary, a[href], [role="button"]');
    if (control && region.contains(control)) press = measure(control);
  };
  const end = () => {
    press = null;
  };
  const reconcile = () => {
    if (!press) return;
    const { control } = press;
    // A viewport reflow (keyboard, rotation) belongs to the browser and the focused-editor handling.
    if (
      !control.isConnected ||
      !region.contains(control) ||
      scroller.clientWidth !== press.width ||
      scroller.clientHeight !== press.height
    ) {
      press = null;
      return;
    }
    const shift = control.getBoundingClientRect().top - press.top;
    if (Math.abs(shift) >= 0.5) scroller.scrollTop += shift;
    press = measure(control);
  };
  // Scrolling during the press (the browser's own anchoring, or this one) is the new baseline, never undone.
  const scrolled = () => {
    if (press?.control.isConnected) press = measure(press.control);
  };
  // Reconcile at the mutation microtask checkpoint, before the release can hit the moved layout.
  const mutations = new MutationObserver(reconcile);
  mutations.observe(region, {
    subtree: true,
    childList: true,
    characterData: true,
    attributes: true,
    attributeFilter: ["class", "style", "hidden"],
  });
  const resizes = new ResizeObserver(reconcile);
  resizes.observe(region);
  const ends = ["pointerup", "pointercancel", "touchmove", "wheel", "keydown"];
  document.addEventListener("pointerdown", start, true);
  for (const name of ends) document.addEventListener(name, end, { capture: true, passive: true });
  scroller.addEventListener("scroll", scrolled);
  return () => {
    press = null;
    mutations.disconnect();
    resizes.disconnect();
    document.removeEventListener("pointerdown", start, true);
    for (const name of ends) document.removeEventListener(name, end, true);
    scroller.removeEventListener("scroll", scrolled);
  };
}
