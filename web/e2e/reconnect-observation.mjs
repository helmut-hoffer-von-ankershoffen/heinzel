// Assertions shared by the installed-window and open-window recovery drivers.
export async function waitForCachedOffline(page, timeout = 30000) {
  await page
    .locator("#boot-hint")
    .filter({ hasText: /could not reach its server|device is offline/i })
    .waitFor({ timeout });
  await page
    .locator("#boot-status")
    // A countdown is scheduled only after an uncached same-origin probe actually failed.
    .filter({ hasText: /^Next try in \d+s$/ })
    .waitFor({ timeout });
  await page.getByRole("button", { name: "Try now" }).waitFor({ timeout });
}

export async function waitForDisconnectedSplash(page) {
  await page.getByRole("heading", { name: "Connection unavailable" }).waitFor({ timeout: 30000 });
}

export async function visibleSplashCount(page) {
  return page.locator("#splash:visible").count();
}
