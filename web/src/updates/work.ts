// Draft owners register preparation; an update cannot assume that replacing the page preserves unsaved work.
const owners = new Set<() => void>();

export function registerUpdatePreparation(prepare: () => void): () => void {
  owners.add(prepare);
  return () => owners.delete(prepare);
}

/** Save every registered draft so that a restart or a reload of the page keeps it; nothing is refused here. */
export function saveOpenWork(): void {
  for (const prepare of owners) prepare();
}

/** Whether a dialog, alert or terminal is open; the dialog that shows an update's own steps is not open work. */
export function workOnScreen(): boolean {
  return document.querySelector('[role="dialog"]:not([data-operation]),[role="alertdialog"],.xterm') !== null;
}

export function preserveUpdateWork(): void {
  if (workOnScreen()) throw new Error("Close the terminal or editor before updating; its open work will stay here.");
  for (const prepare of owners) prepare();
}

// Things that hold off reloading the page after an automatic update (ADR 0116): unsent text in a composer, or a chat
// turn that is still running. The page reloads once none holds.
const holds = new Set<() => boolean>();

export function registerReloadHold(holding: () => boolean): () => void {
  holds.add(holding);
  return () => holds.delete(holding);
}

export function reloadHeld(): boolean {
  for (const holding of holds) if (holding()) return true;
  return false;
}

// The part of a hold that is work on its way: a chat turn running, or a message not yet accepted. Unsent text alone
// can be saved and restored across a reload; this cannot (staleBuild.ts).
const running = new Set<() => boolean>();

export function registerRunningWork(isRunning: () => boolean): () => void {
  running.add(isRunning);
  return () => running.delete(isRunning);
}

export function workRunning(): boolean {
  for (const isRunning of running) if (isRunning()) return true;
  return false;
}

// Unsent input nothing saves (heinzel-management-uke3): a reload drops it, so it holds an automatic reload in front and
// in the background alike. A field counts once the person typed into it and while it still holds text, unless its
// owner saves and restores it across a reload (`data-hz-restorable`: the chat composers). Editors the DOM can't tell
// about (a canvas, a custom widget) register instead.
const typed = new WeakSet<EventTarget>();
if (typeof document !== "undefined") {
  const note = (event: Event) => {
    if (event.target) typed.add(event.target);
  };
  document.addEventListener("input", note, true);
  document.addEventListener("change", note, true);
}
const editors = new Set<() => boolean>();

export function registerUnsavedEditor(isDirty: () => boolean): () => void {
  editors.add(isDirty);
  return () => editors.delete(isDirty);
}

const UNCHECKED = new Set(["checkbox", "radio", "range", "color", "file", "hidden", "button", "submit", "reset"]);

function holdsText(element: Element): boolean {
  if (element instanceof HTMLTextAreaElement) return element.value.trim() !== "";
  if (element instanceof HTMLInputElement) return !UNCHECKED.has(element.type) && element.value.trim() !== "";
  const editable =
    element instanceof HTMLElement &&
    (element.isContentEditable ||
      ["", "true", "plaintext-only"].includes(element.getAttribute("contenteditable") ?? "-"));
  return editable && (element.textContent ?? "").trim() !== "";
}

/** Whether a field or editor holds text the person typed that a reload would lose. */
export function unsavedInput(root: ParentNode = document): boolean {
  for (const isDirty of editors) if (isDirty()) return true;
  for (const element of root.querySelectorAll("input, textarea, [contenteditable]")) {
    if (typed.has(element) && !element.closest("[data-hz-restorable]") && holdsText(element)) return true;
  }
  return false;
}

// A composer that brought back a saved draft says so, and only then may the page say the message is still there.
let restored = false;
const restoreListeners = new Set<() => void>();

export function noteDraftRestored(): void {
  restored = true;
  for (const listener of restoreListeners) listener();
}

export const draftRestored = (): boolean => restored;

/** A new page knows of no restore yet; tests start each case as a new page. */
export function forgetDraftRestored(): void {
  restored = false;
}

export function onDraftRestored(listener: () => void): () => void {
  restoreListeners.add(listener);
  return () => restoreListeners.delete(listener);
}
