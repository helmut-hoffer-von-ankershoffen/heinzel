// Draft owners register preparation; an update cannot assume that replacing the page preserves unsaved work.
const owners = new Set<() => void>();

export function registerUpdatePreparation(prepare: () => void): () => void {
  owners.add(prepare);
  return () => owners.delete(prepare);
}

export function preserveUpdateWork(): void {
  if (document.querySelector('[role="dialog"],[role="alertdialog"],.xterm'))
    throw new Error("Close the terminal or editor before updating; its open work will stay here.");
  for (const prepare of owners) prepare();
}
