// The Settings action uses the real authenticated stream to an isolated daemon manager.
import assert from "node:assert/strict";
import { chromium } from "playwright-core";

const url = new URL(process.argv[2]);
url.searchParams.set("settings", "daemon");
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage();
  const posts = [];
  page.on("request", (request) => {
    const target = new URL(request.url());
    if (request.method() === "POST" && ["/api/stream/system/daemon/restart", "/api/system/daemon/restart"].includes(target.pathname))
      posts.push(`${target.origin}${target.pathname}`);
  });
  await page.goto(url.href);
  const settings = page.getByRole("dialog", { name: "Settings", exact: true });
  await settings.getByRole("button", { name: "Restart", exact: true }).click();
  const dialog = page.getByRole("alertdialog");
  await dialog.getByRole("alert").getByText("The daemon could not restart. Try again in a moment.").waitFor();
  const steps = dialog.getByRole("list", { name: "Steps" });
  await steps.getByText("Checking this is your Mac's desktop session: done").waitFor();
  await steps.getByText("Writing the service definition: done").waitFor();
  await steps.getByText("Loading the owned service definition: failed").waitFor();
  assert.deepEqual(posts, [`${url.origin}/api/stream/system/daemon/restart`]);
  console.log(JSON.stringify({ posts: posts.length, failed: true }));
} finally {
  await browser.close();
}
