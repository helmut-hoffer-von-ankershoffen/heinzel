// Layers are main-process policy, never supplied by a guest or renderer.
const LAYERS = ["browser", "media", "chrome"];
const stacks = new WeakMap();

/** Own attachment order only. Callers retain visibility, bounds and WebContents lifetime.
 * Chrome must be a separate trusted sibling, not BrowserWindow's underlying renderer.
 * This does not enable overlap: existing suppression remains necessary until controls ship.
 */
export function siblingStack(window) {
  if (stacks.has(window)) return stacks.get(window);
  const entries = new Map();
  const stack = {
    attach(view, layer) {
      if (!LAYERS.includes(layer)) throw new Error("Unknown sibling layer.");
      if (window.isDestroyed()) throw new Error("Surface window is closed.");
      if (entries.has(view)) {
        if (entries.get(view) !== layer) throw new Error("Sibling layer cannot change.");
        return;
      }
      window.contentView.addChildView(view);
      entries.set(view, layer);
      // Public View API: adding an existing child moves it to the top without detaching.
      // Only higher layers need promotion; same-layer insertion order remains stable.
      for (const higher of LAYERS.slice(LAYERS.indexOf(layer) + 1)) {
        for (const [sibling, role] of entries) {
          if (role === higher) window.contentView.addChildView(sibling);
        }
      }
    },
    release(view) {
      if (!entries.has(view)) return;
      if (!window.isDestroyed()) window.contentView.removeChildView(view);
      entries.delete(view);
    },
  };
  // Guest owners release on navigation, close and quit; the weak key needs no window listener.
  stacks.set(window, stack);
  return stack;
}
