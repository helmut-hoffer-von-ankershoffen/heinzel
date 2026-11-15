// Shared e2e wait: let a scroll the app started (smooth, unless reduced motion) finish before a check measures where an
// element sits. A box measured mid-scroll is neither where the person ends up nor a layout fault.

/**
 * Resolve once `locator`'s box has stayed the same for `quiet` ms, also when the app's scroll starts a little after
 * the action that asked for it; `scrollend` from any of its scrolling ancestors ends the wait early once the box moved.
 * Gives up after `timeout` ms and leaves the verdict to the caller's own assertion.
 */
export async function scrollSettled(locator, { quiet = 500, timeout = 4000 } = {}) {
  await locator.evaluate(
    (element, { quiet, timeout }) =>
      new Promise((resolve) => {
        const scrollers = [];
        for (let node = element.parentElement; node; node = node.parentElement)
          if (/(auto|scroll)/.test(getComputedStyle(node).overflowY)) scrollers.push(node);
        scrollers.push(document.scrollingElement);
        const started = performance.now();
        let last = JSON.stringify(element.getBoundingClientRect());
        let changedAt = started;
        let moved = false;
        let done = false;
        const finish = () => {
          if (done) return;
          done = true;
          for (const node of scrollers) node?.removeEventListener("scrollend", ended);
          resolve();
        };
        const ended = () => {
          if (moved) requestAnimationFrame(() => requestAnimationFrame(finish));
        };
        for (const node of scrollers) node?.addEventListener("scrollend", ended);
        const frame = (now) => {
          if (done) return;
          const box = JSON.stringify(element.getBoundingClientRect());
          if (box !== last) {
            last = box;
            changedAt = now;
            moved = true;
          }
          if (now - changedAt >= quiet || now - started >= timeout) finish();
          else requestAnimationFrame(frame);
        };
        requestAnimationFrame(frame);
      }),
    { quiet, timeout },
  );
}
