// Sizing helpers for the terminal panel. The app never types a command into the terminal for the person.

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
