// Offline browser fixture: no server, installed app, provider or downloaded browser.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import { chromium } from "playwright-core";
import { visibleSplashCount, waitForCachedOffline, waitForDisconnectedSplash } from "./reconnect-observation.mjs";

test("cached page reports a real pending/failing probe and mounted splash clears", async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    await page.route("**/*", (route) => route.abort());
    await page.setContent('<p role="status">hz isn\'t answering</p>');
    await assert.rejects(waitForCachedOffline(page, 100)); // a stale page cannot satisfy this build's evidence
    const html = await readFile("public/offline.html", "utf8");
    const script = await readFile("public/boot-retry.js", "utf8");
    await page.setContent(html.replace(/<script[^>]*><\/script>/, ""));
    await page.evaluate(() => {
      window.requests = [];
      window.fetch = (url, options) => {
        window.requests.push({ url, cache: options.cache, credentials: options.credentials });
        return Promise.resolve(new Response("", { status: 503 }));
      };
    });
    await page.addScriptTag({ content: script });
    await page.evaluate(() => window.hzBootRetry.start("offline"));
    await waitForCachedOffline(page);
    assert.match(await page.locator("#boot-status").textContent(), /^Next try in \d+s$/);
    const calls = await page.evaluate(() => window.requests);
    assert.ok(calls.length >= 1);
    assert.match(calls[0].url, /^\/manifest\.webmanifest\?hz-probe=\d+$/);
    assert.equal(calls[0].cache, "no-store");
    assert.equal(calls[0].credentials, "omit");
    await page.setContent(
      '<div id="splash"><h1>Connection unavailable</h1></div><footer role="contentinfo">Signed in</footer>',
    );
    await waitForDisconnectedSplash(page);
    assert.equal(await visibleSplashCount(page), 1);
    await page.locator("#splash").evaluate((splash) => {
      splash.hidden = true;
    });
    assert.equal(await visibleSplashCount(page), 0);
    assert.equal(await page.getByRole("contentinfo").count(), 1);
  } finally {
    await browser.close();
  }
});
