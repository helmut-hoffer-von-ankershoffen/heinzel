// Poll from Node until an asynchronous check holds. page.waitForFunction cannot do this: it takes the promise an async
// predicate returns as a truthy result and stops after one call, whatever the promise resolves to.
export async function until(check, { timeout = 30_000, interval = 100, message }) {
  const deadline = Date.now() + timeout;
  for (;;) {
    const value = await check();
    if (value) return value;
    if (Date.now() > deadline) throw new Error(message);
    await new Promise((resolve) => setTimeout(resolve, interval));
  }
}
