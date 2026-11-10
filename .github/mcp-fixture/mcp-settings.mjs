import assert from "node:assert/strict";
import { chromium } from "playwright-core";
import { createMcpHandoffHandler } from "../../desktop/src/mcp-handoff-ipc.js";

const url = new URL(process.argv[2]);
assert.equal(url.hostname, "127.0.0.1");
assert.equal(url.protocol, "http:");
assert.ok(url.port);
const connection = { origin: url.origin, token: new URLSearchParams(url.hash.slice(1)).get("token") };
assert.ok(connection.token);
url.pathname = "/settings";

const copies = [];
const lookups = [];
const owner = { isDestroyed: () => false };
const frame = { isDestroyed: () => false, processId: 1, frameToken: "fixture-main", detached: false, url: url.origin };
const sender = { mainFrame: frame, isDestroyed: () => false };
const handoff = createMcpHandoffHandler({
  windows: new WeakSet([owner]),
  fromWebContents: (candidate) => (candidate === sender ? owner : null),
  connection: () => connection,
  fetchPlan: (address, options) => {
    assert.equal(new URL(address).origin, connection.origin);
    lookups.push(new URL(address).pathname);
    return fetch(address, options);
  },
  prompt: async (window, options) => {
    assert.equal(window, owner);
    assert.deepEqual(options.buttons, ["Cancel", "Copy command"]);
    return { response: 1 };
  },
  copy: (command) => copies.push(command),
});

const browser = await chromium.launch({ headless: true, chromiumSandbox: true });
try {
  const context = await browser.newContext({
    locale: "en-US",
    serviceWorkers: "block",
    viewport: { width: 1280, height: 900 },
  });
  await context.route("**/*", (route) =>
    new URL(route.request().url()).origin === url.origin ? route.continue() : route.abort("blockedbyclient"),
  );
  await context.routeWebSocket("**/*", (socket) => {
    const address = new URL(socket.url());
    if (address.protocol === "ws:" && address.host === url.host) socket.connectToServer();
    else socket.close();
  });
  const page = await context.newPage();
  page.setDefaultTimeout(12_000);
  await page.exposeBinding("fixtureMcpHandoff", (source, id) => {
    assert.equal(source.frame, page.mainFrame());
    frame.url = source.frame.url();
    return handoff({ sender, senderFrame: frame }, id);
  });
  await page.addInitScript(() => {
    // Use the real handler with synthetic window, dialog and clipboard ports. There is no Electron process.
    window.heinzelDesktop = { mcpHandoff: (id) => window.fixtureMcpHandoff(id) };
  });

  await page.goto(url.href);
  await page.locator("#splash").waitFor({ state: "hidden" });
  await page.getByRole("button", { name: "MCP connections", exact: true }).click();
  const panel = page.getByRole("region", { name: "MCP connections", exact: true });
  const registration = panel.getByLabel("Registration ID");
  const readStatus = panel.getByRole("button", { name: "Read status" });
  const openHandoff = panel.getByRole("button", { name: "Open terminal handoff" });

  async function read(id, worker) {
    await registration.fill(id);
    await readStatus.click();
    await panel.getByText(worker, { exact: true }).waitFor();
  }

  async function prepare(action) {
    await panel.getByRole("button", { name: `Prepare ${action}`, exact: true }).click();
    await openHandoff.waitFor();
    const copied = panel.getByText("Command copied for local review.", { exact: true });
    await copied.waitFor({ state: "hidden" });
  }

  async function copy(pattern) {
    const previous = copies.length;
    await openHandoff.click();
    await panel.getByText("Command copied for local review.", { exact: true }).waitFor();
    assert.equal(copies.length, previous + 1);
    assert.match(copies.at(-1), pattern);
  }

  await read("fixture-dormant", "Stopped");
  await prepare("Start");
  await copy(/^hz system mcp start fixture-dormant --handoff [1-9][0-9]*\.[a-f0-9]{64}$/);
  await panel.getByText("Stopped", { exact: true }).waitFor();

  await read("fixture-owned", "Running");
  await prepare("Check");
  await copy(/^check --handoff [1-9][0-9]*\.[a-f0-9]{64}$/);
  await prepare("Stop");
  await copy(/^stop --handoff [1-9][0-9]*\.[a-f0-9]{64}$/);
  await panel.getByText("Running", { exact: true }).waitFor();

  await read("fixture-dormant", "Stopped");
  await prepare("Install");
  const install = /^hz system mcp review-install fixture-dormant --handoff [1-9][0-9]*\.[a-f0-9]{64}$/;
  await copy(install);
  await copy(install);
  assert.equal(copies.at(-1), copies.at(-2));
  assert.equal(lookups.at(-1), lookups.at(-2));
  assert.equal(lookups.length, 5);
} finally {
  await browser.close();
}
