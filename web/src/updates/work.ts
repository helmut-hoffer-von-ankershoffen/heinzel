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

export function preserveUpdateWork(): void {
  // The dialog that shows this update's own steps is not open work.
  if (document.querySelector('[role="dialog"]:not([data-operation]),[role="alertdialog"],.xterm'))
    throw new Error("Close the terminal or editor before updating; its open work will stay here.");
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
