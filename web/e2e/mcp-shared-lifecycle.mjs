// The mounted Settings pane and confirmation dialog call the actual private web operations.
import assert from "node:assert/strict";
import { chromium, webkit } from "playwright-core";

const [, , address, engine] = process.argv;
const url = new URL(address);
const browser = await (engine === "webkit" ? webkit : chromium).launch({ chromiumSandbox: true });
try {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, serviceWorkers: "block" });
  const blocked = [];
  await context.route("**/*", (route) => {
    const target = new URL(route.request().url());
    if (target.origin === url.origin || ["data:", "blob:"].includes(target.protocol)) return route.continue();
    blocked.push(target.origin);
    return route.abort();
  });
  await context.routeWebSocket("**/*", (socket) => {
    const target = new URL(socket.url());
    if (target.protocol === "ws:" && target.host === url.host) return socket.connectToServer();
    blocked.push(target.origin);
    return socket.close();
  });
  const page = await context.newPage();
  page.setDefaultTimeout(12000);
  const posts = [];
  page.on("request", (request) => {
    const target = new URL(request.url());
    if (request.method() === "POST" && target.pathname.startsWith("/api/stream/system/mcp/")) {
      posts.push([target.pathname, target.search, request.postDataJSON()]);
    }
  });
  await page.goto(url.href);
  await page.locator("#splash").waitFor({ state: "hidden" });
  const setup = page.getByRole("dialog", { name: "Set up hz" });
  await setup.waitFor({ state: "visible", timeout: 5000 }).catch(() => {});
  if (await setup.isVisible()) await setup.getByRole("button", { name: "Close setup" }).click();
  const platform = await page.evaluate(() => navigator.platform);
  await page.keyboard.press(platform.startsWith("Mac") ? "Meta+," : "Control+,");
  await page.getByRole("combobox", { name: "Settings page" }).selectOption("managed-mcp");
  const pane = page.getByRole("region", { name: "Managed MCP server" });
  await pane.getByText("This profile has not started a managed server.").waitFor();

  const start = pane.getByRole("button", { name: "Start managed server" });
  const startDialog = page.getByRole("dialog", { name: /Start one local MCP server for this profile/ });
  await start.click();
  await startDialog.getByText(/will not start at login/).waitFor();
  assert.deepEqual(posts, []);
  await startDialog.getByRole("button", { name: "Cancel" }).click();
  assert.deepEqual(posts, []);
  await start.click();
  await startDialog.getByRole("button", { name: "Run it" }).click();
  await startDialog.getByRole("button", { name: "Done" }).click();
  const ready = pane.getByText(/The owned server authenticated and initialized with [1-9]\d* tools/);
  await ready.waitFor();

  const checked = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/system/mcp/check");
  await pane.getByRole("button", { name: "Check managed server" }).click();
  assert.equal((await checked).ok(), true);
  await ready.waitFor();

  await pane.getByRole("button", { name: "Stop managed server" }).click();
  const stopDialog = page.getByRole("dialog", { name: /Stop only this profile's owned MCP server/ });
  assert.equal(posts.length, 1);
  await stopDialog.getByRole("button", { name: "Run it" }).click();
  await stopDialog.getByRole("button", { name: "Done" }).click();
  await pane.getByText(/listener is closed\. Descendant processes have not been verified/).waitFor();
  assert.deepEqual(posts, [
    ["/api/stream/system/mcp/start", "", { confirm: true }],
    ["/api/stream/system/mcp/stop", "", { confirm: true }],
  ]);
  assert.deepEqual(blocked, []);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
} finally {
  await browser.close();
}
