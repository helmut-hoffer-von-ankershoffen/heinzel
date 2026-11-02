// A plugin installed outside the running app (a terminal's `hz system plugin add`) shows Restart and apply in the footer.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { chromium } from "playwright-core";

const [url, hzBin, pluginDir, shots] = process.argv.slice(2);
const browser = await chromium.launch();
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  await page.goto(url);
  await page.getByRole("contentinfo").waitFor({ timeout: 30000 });
  assert.equal(await page.getByRole("button", { name: /^Plugins changed/ }).count(), 0, "nothing is pending yet");
  // The real thing: a terminal's `hz system plugin develop` into the very environment this server runs from.
  execFileSync(hzBin, ["system", "plugin", "develop", pluginDir], { stdio: "pipe", timeout: 180000 });
  await page.reload();
  const item = page.getByRole("button", { name: /^Plugins changed/ });
  await item.waitFor({ timeout: 60000 });
  await item.click();
  await page.getByText("heinzel-sidecar was installed").waitFor({ timeout: 8000 });
  await page.getByRole("button", { name: "Restart and apply" }).waitFor();
  await page.screenshot({ path: `${shots}/plugins-pending.png` });
  console.log(JSON.stringify({ pending: true }));
} finally {
  await browser.close();
}
