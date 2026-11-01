/** Asks the shell to deal with a command the app cannot run itself (`CommandHost`). */
export const SHOW_COMMAND = "hz:show-command";

export function showCommand(command: string): void {
  window.dispatchEvent(new CustomEvent(SHOW_COMMAND, { detail: command }));
}
