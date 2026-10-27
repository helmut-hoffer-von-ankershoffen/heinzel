// Exercise the last animated pixel in real Chromium: it can change which measured footer items fit.
import assert from "node:assert/strict";
import { chromium } from "playwright-core";
import { navigationFolded, waitForNavigationFold } from "./footer.mjs";

let browser;
try {
  browser = await chromium.launch();
} catch {
  browser = await chromium.launch({ channel: "chrome" });
}
try {
  const page = await browser.newPage();
  await page.setContent(
    '<div style="display:flex"><aside style="width:256px;height:100px"></aside><footer style="flex:1"></footer></div>',
  );
  await page.evaluate(() => {
    const footer = document.querySelector("footer");
    new ResizeObserver(() => {
      requestAnimationFrame(() => {
        window.measuredFooterWidth = footer.getBoundingClientRect().width;
      });
    }).observe(footer);
  });
  const during = await page.evaluate(() => {
    const sidebar = document.querySelector("aside");
    const animation = sidebar.animate([{ width: "256px" }, { width: "56px" }], {
      duration: 200,
      fill: "forwards",
    });
    animation.pause();
    animation.currentTime = 199.5;
    return { width: sidebar.getBoundingClientRect().width, state: animation.playState };
  });
  assert.ok(during.width > 56 && during.width <= 57, JSON.stringify(during));
  assert.equal(during.state, "paused");
  assert.equal(await page.evaluate(navigationFolded), false, "A nearly folded sidebar still changes footer capacity.");
  await page.evaluate(() => document.querySelector("aside").getAnimations()[0].finish());
  await waitForNavigationFold(page);
  assert.equal(
    await page.evaluate(
      () => window.measuredFooterWidth === document.querySelector("footer").getBoundingClientRect().width,
    ),
    true,
    "The final width must also reach ResizeObserver's deferred footer measurement.",
  );
  console.log(JSON.stringify({ nearFinalWidth: during.width, waitedForFinishedAnimation: true }));
} finally {
  await browser.close();
}
