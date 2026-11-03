// A probe of a page that the app may reload under it. A window that outlives `hz ui` reloads itself once the new daemon
// refuses its old session token (src/api/device.ts); a page.evaluate or locator call in flight at that moment throws
// "Execution context was destroyed", which is not an answer about the app. Polls treat it as "not yet" and ask again.
const NAVIGATED = /Execution context was destroyed|because of a navigation|Frame was detached/;

export async function steady(probe) {
  try {
    return await probe();
  } catch (error) {
    if (NAVIGATED.test(String(error?.message ?? error))) return undefined;
    throw error;
  }
}
