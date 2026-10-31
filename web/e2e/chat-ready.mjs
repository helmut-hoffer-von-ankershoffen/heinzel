import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { chromium, webkit } from "playwright-core";

const require = createRequire(import.meta.url);

/** The phone-sized browser a chat journey runs in, at /doctor: argv is the app's URL and the browser engine. */
export async function openChatJourney() {
  const url = new URL(process.argv[2]);
  const token = url.hash.match(/(?:^#|&)token=([^&]+)/)?.[1] ?? "";
  url.pathname = "/doctor";
  const browser = await (process.argv[3] === "webkit" ? webkit : chromium).launch({ headless: true });
  const context = await browser.newContext({ serviceWorkers: "block", viewport: { width: 390, height: 844 } });
  const page = await context.newPage();
  const axe = readFileSync(require.resolve("axe-core/axe.min.js"), "utf8");
  return { axe, browser, context, page, token, url };
}

// After a reload, the Chat tab sits below page content that is still loading, so a click can land on what moved under
// the pointer and do nothing. Wait for startup to end, then press the tab: that needs no position. Require that Chat
// really is selected and shown.
export async function revealChat(page) {
  await page.locator("#splash").waitFor({ state: "hidden" });
  await page.waitForFunction(() => {
    const root = document.getElementById("root");
    return root !== null && !root.hasAttribute("inert");
  });
  await page.getByRole("tab", { name: "Chat", exact: true }).press("Enter");
  await page.getByRole("tab", { name: "Chat", exact: true, selected: true }).waitFor();
  await page.getByRole("tabpanel", { name: "Chat", exact: true }).waitFor();
}
