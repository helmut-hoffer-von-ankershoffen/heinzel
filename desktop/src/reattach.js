/** How long the app waits between attempts to attach again after its owned service was lost (bounded backoff). */
export const REATTACH_DELAYS_MS = Object.freeze([5_000, 10_000, 20_000, 40_000]);

/**
 * Attach again after the owned service stopped answering: now, then after each delay. True once an attempt
 * connects; false when every attempt failed or the app is quitting, so the caller shows the connection screen
 * with its Try Again action.
 */
export async function reattach(attempt, {
  delays = REATTACH_DELAYS_MS,
  wait = (ms) => new Promise((done) => { setTimeout(done, ms).unref?.(); }),
  cancelled = () => false,
} = {}) {
  for (let index = 0; index <= delays.length; index++) {
    if (cancelled()) return false;
    try { if (await attempt()) return true; } catch { /* The next attempt follows the next delay. */ }
    if (index < delays.length) await wait(delays[index]);
  }
  return false;
}
