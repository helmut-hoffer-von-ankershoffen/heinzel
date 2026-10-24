// Commands the app hands to the terminal panel: a command chip's "Run" opens the panel and types the command there.
// The panel registers a sender once its shell is connected; until then commands wait here.

type Sender = (text: string) => void;
let sender: Sender | null = null;
const pending: string[] = [];
export const OPEN_TERMINAL = "hz:open-terminal";

/**
 * Whether `command` runs as it stands: `hz` and plain words, or single-quoted arguments as `configValue` writes them.
 * Anything else (a value from a team repository with `;` or `$(`) is typed but left for the person to press Enter.
 */
export function runsAsIs(command: string): boolean {
  return /^hz( +([\w./:=@,+-]+|'([^']|'\\'')*'))*$/.test(command);
}

/** `command` as one line: a line break, an escape or another control character inside would run or rewrite what
 * comes before it, so each becomes a space. */
function oneLine(command: string): string {
  // biome-ignore lint/suspicious/noControlCharactersInRegex: removing them is the point
  return command.replace(/[\u0000-\u001f\u007f-\u009f]/g, " ");
}

function send(text: string): void {
  if (sender) sender(text);
  else pending.push(text);
  window.dispatchEvent(new Event(OPEN_TERMINAL));
}

/** Type `command` into the terminal, opening it; Enter only when `runsAsIs`. */
export function runInTerminal(command: string): void {
  const line = oneLine(command);
  send(runsAsIs(line) ? `${line}\r` : line);
}

/**
 * Type `command` into the terminal, opening it, and never press Enter: the person reads it and runs it, as for a
 * connector's sign-in, whose text comes from a plugin (ADR 0055).
 */
export function offerInTerminal(command: string): void {
  send(oneLine(command));
}

/** The connected terminal's sender, or null when it closes; waiting commands go out on connect. */
export function connectTerminal(send: Sender | null): void {
  sender = send;
  if (send) for (const text of pending.splice(0)) send(text);
}
