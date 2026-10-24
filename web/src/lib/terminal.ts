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

/** Type `command` into the terminal, opening it; Enter only when `runsAsIs`. */
export function runInTerminal(command: string): void {
  // A line break or other control character inside would run what comes before it: they become spaces.
  const line = [...command]
    .map((char) => (char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127 ? " " : char))
    .join("");
  const text = runsAsIs(line) ? `${line}\r` : line;
  if (sender) sender(text);
  else pending.push(text);
  window.dispatchEvent(new Event(OPEN_TERMINAL));
}

/** Type `command` into the terminal, opening it, and never press Enter: the person reads it and decides. */
export function typeInTerminal(command: string): void {
  if (sender) sender(command);
  else pending.push(command);
  window.dispatchEvent(new Event(OPEN_TERMINAL));
}

/** The connected terminal's sender, or null when it closes; waiting commands go out on connect. */
export function connectTerminal(send: Sender | null): void {
  sender = send;
  if (send) for (const text of pending.splice(0)) send(text);
}
