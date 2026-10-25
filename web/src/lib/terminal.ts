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
  // biome-ignore lint/suspicious/noControlCharactersInRegex: control characters are what it refuses
  return /^hz( +([\w./:=@,+-]+|'([^'\u0000-\u001f\u007f-\u009f]|'\\'')*'))*$/.test(command);
}

/** `text` with control characters as spaces: a terminal acts on them (Ctrl-C, Enter, escapes) wherever they are. */
// biome-ignore lint/suspicious/noControlCharactersInRegex: control characters are what it replaces
const withoutControls = (text: string) => text.replace(/[\u0000-\u001f\u007f-\u009f]/g, " ");

/** Type `command` into the terminal, opening it; Enter only when `runsAsIs`. */
export function runInTerminal(command: string): void {
  // A line break or other control character inside would run what comes before it: they become spaces.
  const line = withoutControls(command);
  const text = runsAsIs(line) ? `${line}\r` : line;
  if (sender) sender(text);
  else pending.push(text);
  window.dispatchEvent(new Event(OPEN_TERMINAL));
}

/** Type `command` into the terminal, opening it, and never press Enter: the person reads it and decides. */
export function typeInTerminal(command: string): void {
  const typed = withoutControls(command);
  if (sender) sender(typed);
  else pending.push(typed);
  window.dispatchEvent(new Event(OPEN_TERMINAL));
}

/** The connected terminal's sender, or null when it closes; waiting commands go out on connect. */
export function connectTerminal(send: Sender | null): void {
  sender = send;
  if (send) for (const text of pending.splice(0)) send(text);
}

type Fitting = { cols: number; rows: number; refresh: (start: number, end: number) => void };

/**
 * Keep a terminal the size of its container: after layout (a frame) and debounced, fit it, redraw it and tell the shell
 * its new columns and rows. Watches the container and the window; call the returned `refit` after a layout change the
 * observers can't see (full screen, a tab shown). A hidden container is measured when it is shown.
 */
export function followSize(
  element: HTMLElement,
  terminal: Fitting,
  fit: { fit: () => void },
  send: (cols: number, rows: number) => void,
): { refit: () => void; dispose: () => void } {
  let timer: ReturnType<typeof setTimeout> | undefined;
  let frame = 0;
  const now = () => {
    frame = 0;
    if (element.offsetParent === null) return;
    fit.fit();
    terminal.refresh(0, Math.max(terminal.rows - 1, 0));
    send(terminal.cols, terminal.rows);
  };
  const refit = () => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(now);
    }, 50);
  };
  const observer = new ResizeObserver(refit);
  observer.observe(element);
  window.addEventListener("resize", refit);
  return {
    refit,
    dispose: () => {
      clearTimeout(timer);
      cancelAnimationFrame(frame);
      observer.disconnect();
      window.removeEventListener("resize", refit);
    },
  };
}
