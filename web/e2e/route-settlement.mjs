// A held fixture response must be delivered while its request context still exists.
export async function settleRoutes(page, release) {
  release?.();
  await page.unrouteAll({ behavior: "wait" });
}

// Close a page, context or browser whose routes proxy with route.fetch and route.fulfill. Closing can dispose a
// still-pending fetch or its response before the handler fulfils it. Unroute first with "ignoreErrors": only handler
// errors raised after unrouting, that is while closing, are ignored; every earlier failure still surfaces.
export async function closeRouted(target) {
  const isPage = typeof target.contexts !== "function" && typeof target.pages !== "function";
  const contexts = isPage ? [] : typeof target.contexts === "function" ? target.contexts() : [target];
  const pages = isPage ? [target] : contexts.flatMap((context) => context.pages());
  for (const page of pages) await page.unrouteAll({ behavior: "ignoreErrors" });
  for (const context of contexts) await context.unrouteAll({ behavior: "ignoreErrors" });
  await target.close();
}
