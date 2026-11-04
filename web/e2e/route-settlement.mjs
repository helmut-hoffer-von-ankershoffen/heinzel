// A held fixture response must be delivered while its request context still exists.
export async function settleRoutes(page, release) {
  release?.();
  await page.unrouteAll({ behavior: "wait" });
}
