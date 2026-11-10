// Observe a real doctor notice, then allow the isolated fixture's health check to recover.
import assert from "node:assert/strict";
import { writeFileSync } from "node:fs";
import { chromium } from "playwright-core";

const [url, marker] = process.argv.slice(2);
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();
try {
  await page.addInitScript(() => {
    // Isolate the event path from useNotices' 30-second refetch interval. Merely
    // waiting less than 30 seconds after recovery is insufficient: startup may
    // already have consumed part of that interval.
    const setInterval = window.setInterval.bind(window);
    window.__noticePollsSuppressed = 0;
    window.setInterval = (callback, delay, ...args) => {
      if (delay === 30_000) {
        window.__noticePollsSuppressed += 1;
        return setInterval(callback, 86_400_000, ...args);
      }
      return setInterval(callback, delay, ...args);
    };
    window.__noticeEvents = 0;
    window.addEventListener("hz:notices", () => {
      window.__noticeEvents += 1;
    });
  });
  await page.goto(url);
  await page.getByRole("heading", { name: "Today" }).waitFor();
  const widget = page.locator('[data-widget="notifications"]');
  await widget.getByText("Heinzelmännchen is degraded").waitFor();
  await widget.getByText("A newer notice").waitFor();
  assert.ok(await page.evaluate(() => window.__noticePollsSuppressed > 0), "notice polling must be suppressed");
  await page.waitForFunction(() => window.__noticeEvents > 0);
  const beforeRecovery = await page.evaluate(() => window.__noticeEvents);
  writeFileSync(marker, "observed");
  await page.waitForFunction((before) => window.__noticeEvents > before, beforeRecovery, { timeout: 10000 });
  await widget.getByText("Heinzelmännchen is degraded").waitFor({ state: "hidden", timeout: 10000 });
  assert.equal(await widget.getByText("A newer notice").isVisible(), true);
  console.log(JSON.stringify({ resolved: true }));
} finally {
  await browser.close();
}
