// The browser journey (ADR 0162, the phase-1 release gate): an agent drives the built-in browser of the real app.
//   node e2e/browser-journey.mjs heinzel   a chat turn on a scripted model that calls hz's tools directly
//   node e2e/browser-journey.mjs mcp       an outside agent over /mcp on the web server, with a write token
// Real Electron, real `hz ui serve`, real bridge and guests; the app stays hidden, and the only site is a fixture on
// loopback served under two made-up public names (browser-journey-site.mjs). HZ_JOURNEY_PYTHON is the Python of
// this checkout's environment (`.venv/bin/python`).
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { constants } from "node:fs";
import { access, mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, isAbsolute, join, relative, resolve } from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { fileURLToPath } from "node:url";
import { _electron as electron } from "../../web/node_modules/playwright-core/index.mjs";
import { cleanupHarness, withTimeout } from "./harness-cleanup.mjs";
import { SHOP, WIDGET, startSite } from "./browser-journey-site.mjs";

const journey = process.argv[2];
if (journey !== "heinzel" && journey !== "mcp") throw new Error("Say which journey: heinzel or mcp.");
const repository = fileURLToPath(new URL("../../", import.meta.url));
const python = process.env.HZ_JOURNEY_PYTHON;
if (!python || !isAbsolute(python)) throw new Error("Set HZ_JOURNEY_PYTHON to the absolute Python of this checkout's environment.");
await access(python, constants.X_OK);
const imported = spawnSync(python, ["-I", "-c", "import heinzel; print(heinzel.__file__)"], { encoding: "utf8" });
assert.equal(imported.status, 0, `HZ_JOURNEY_PYTHON cannot import heinzel: ${imported.stderr}`);
assert.ok(!relative(join(repository, "src"), imported.stdout.trim()).startsWith(".."),
  `HZ_JOURNEY_PYTHON runs another checkout's heinzel (${dirname(imported.stdout.trim())}); the journey must test this one.`);
await access(join(repository, "src/heinzel/heads/web/static/index.html")).catch(() => {
  throw new Error("The web app is not built: run `uv run --script scripts/web_build.py build` first.");
});
const electronDirectory = fileURLToPath(new URL("../node_modules/electron/", import.meta.url));
const electronDist = join(electronDirectory, "dist");
const electronPath = (await readFile(join(electronDirectory, "path.txt"), "utf8")).trim();
const electronExecutable = resolve(electronDist, electronPath);
if (!electronPath || isAbsolute(electronPath) || relative(electronDist, electronExecutable).startsWith(".."))
  throw new Error("The locked Electron executable path is invalid.");
await access(electronExecutable, constants.X_OK);

const SHOP_URL = `https://${SHOP}/`;
const NOT_PUBLIC = ["https://127.0.0.1/", "https://localhost/", `http://${SHOP}/`, "https://192.168.1.10/"];
const root = await mkdtemp(join(tmpdir(), "hz-browser-journey-"));
const dirs = ["bin", "home", "config", "data", "state", "cache", "logs", "electron-profile", "electron-session", "electron-logs", "tmp", "sync", "site"];
let app, electronProcess, ownedPid, site;
let stopped = false, launchAttempted = false;

const refused = (text) => typeof text === "string" && text.startsWith("Error:");
const proposed = (text) => typeof text === "string" && text.startsWith("Not run:");
const ran = (text) => typeof text === "string" && !refused(text) && !proposed(text) && /t-[0-9a-f]{8}/.test(text);
const tabOf = (text) => /t-[0-9a-f]{8}/.exec(text)?.[0] ?? "";
const refOf = (text, role, name) =>
  new RegExp(`- ${role} \\\\?"${name}\\\\?"[^\\[]{0,120}\\[(ref_\\d+)\\]`).exec(text)?.[1] ?? "ref_0";
/** Page events arrive a moment after the input; wait for the ones expected, then give a stray one time to show. */
async function settled(kind, count) {
  for (let turn = 0; turn < 40 && site.count(kind) < count; turn += 1) await delay(250);
  await delay(750);
}

/** One call of the web API from the product page, as that page makes it: with its session token. */
function api(page, method, path, { query = {}, body } = {}) {
  return withTimeout(page.evaluate(async (call) => {
    const url = new URL(`/api${call.path}`, location.origin);
    for (const [key, value] of Object.entries(call.query)) url.searchParams.set(key, value);
    const response = await fetch(url, {
      method: call.method,
      headers: { Authorization: `Bearer ${sessionStorage.getItem("hz.token")}`, ...(call.body ? { "Content-Type": "application/json" } : {}) },
      body: call.body ? JSON.stringify(call.body) : undefined,
    });
    const text = await response.text();
    let json = null;
    try { json = JSON.parse(text); } catch { /* not JSON */ }
    return { status: response.status, json, text: text.slice(0, 600) };
  }, { method, path, query, body }), 240_000, `The web API did not answer ${path}.`);
}
/** What the person does in the browser panel: the product page's own `hz:browser` call. */
const panel = (page, request) => page.evaluate((command) => window.heinzelDesktop.browser(command), request);
const agentTabs = async (page) => (await panel(page, { action: "tabs" })).tabs.filter((tab) => tab.agent);

/** What both journeys must have left behind once the agent did the free and the forbidden steps. */
async function assertInputs(tab) {
  await settled("press:mousedown", 1);
  assert.equal(site.count("note:click"), 1, "The ordinary button must be clicked once, by a trusted click.");
  assert.equal(site.count("hover:swapped"), 1, "The page must have slid its frame in when the pointer moved.");
  assert.equal(site.count("hover:mousedown") + site.count("hover:click"), 0, "Nothing may be pressed once a frame covers the target.");
  assert.equal(site.count("widget-hover:mousedown") + site.count("widget-hover:click"), 0, "The covering frame must get no press.");
  assert.equal(site.count("press:swapped"), 1, "The page must have slid its frame in on the press.");
  assert.equal(site.count("press:mousedown"), 1, "The press must have reached the described button.");
  assert.equal(site.count("press:click"), 0, "A press that the page answered with a swap must not become a click.");
  assert.equal(site.count("press:mouseup"), 0, "The release must not land on the button.");
  assert.equal(site.count("widget-press:click") + site.count("widget-press:mouseup"), 0, "The frame slid under the pointer must get no release and no click.");
  assert.deepEqual(site.state.orders, [], "Nothing may be sent before the person's yes.");
  assert.equal(site.count("send:click"), 0, "The commit must wait.");
  assert.ok(tab, "The agent must have opened a tab.");
}
function assertNothingElseDialled() {
  const others = site.state.connects.filter((target) => target !== `${SHOP}:443` && target !== `${WIDGET}:443`);
  assert.deepEqual(others, [], "The guest may dial the fixture's two names only: no refused address reached the network.");
  assert.ok(site.state.connects.includes(`${SHOP}:443`), "The guest must have loaded the shop through its proxy.");
}

async function heinzelJourney(page, origin) {
  const off = await fetch(`${origin}/mcp`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
  assert.equal(off.status, 404, "MCP for outside agents must be off: a Heinzel calls its tools directly.");
  const opened = await api(page, "POST", "/system/runner/chat/open");
  assert.equal(opened.status, 200, `The chat did not open: ${opened.text}`);
  const session = (opened.json.result ?? opened.json).id;
  const turn = async (message) => {
    const sent = await api(page, "POST", "/system/runner/chat/send", { query: { session, message } });
    assert.equal(sent.status, 200, `The turn failed: ${sent.text}`);
    const chat = sent.json.result ?? sent.json;
    const answer = chat.messages.at(-1);
    assert.equal(answer.status, "completed", `The turn did not complete: ${answer.detail}`);
    assert.equal(answer.runner, "journey");
    assert.ok(Object.keys(answer.tools).every((name) => name.startsWith("mcp__hz__ui_browser_")));
    return { chat, report: JSON.parse(answer.text) };
  };

  const first = await turn("JOURNEY-TURN-1 Please send the tea order for Mira.");
  const report = first.report;
  assert.match(report.tabs, /"available": ?true/, `The desktop's bridge never connected: ${report.tabs}`);
  assert.ok(refused(report.open_not_public), `A tab on a literal address must be refused: ${report.open_not_public}`);
  assert.ok(report.read.includes("Add a gift note") && /\[ref_\d+\]/.test(report.read), `The page must be read with refs: ${report.read}`);
  for (const address of NOT_PUBLIC) assert.ok(refused(report.not_public[address]), `${address} must be refused: ${report.not_public[address]}`);
  assert.ok(ran(report.type_name), `Typing a name must run: ${report.type_name}`);
  assert.ok(ran(report.click_note), `An ordinary click must run: ${report.click_note}`);
  assert.ok(refused(report.type_password), `A password field must be refused: ${report.type_password}`);
  assert.ok(refused(report.type_card), `A card field must be refused: ${report.type_card}`);
  assert.ok(refused(report.click_hover_swap), `A covered target must be refused: ${report.click_hover_swap}`);
  assert.ok(refused(report.click_press_swap), `A target swapped on the press must be refused: ${report.click_press_swap}`);
  assert.ok(proposed(report.send), `Sending the form is a commit and must be proposed: ${report.send}`);
  await assertInputs(report.tab);
  const [tab] = await agentTabs(page);
  assert.equal(tab?.id, report.tab);
  assert.equal(tab.agent.kind, "heinzel");
  assert.equal(tab.url, SHOP_URL, "No refused address may have moved the tab.");

  // The person's yes: what the chat's confirmation card sends for the proposed action.
  const [action] = first.chat.actions.filter((entry) => entry.status === "pending");
  assert.ok(action && action.tool === "ui_browser_click", `One pending browser click is expected: ${JSON.stringify(first.chat.actions)}`);
  const confirmed = await api(page, "POST", "/system/runner/chat/confirm", { query: { session, action: action.id } });
  assert.equal(confirmed.status, 200, `The confirmation failed: ${confirmed.text}`);
  const done = (confirmed.json.result ?? confirmed.json).actions.find((entry) => entry.id === action.id);
  assert.equal(done.status, "done", `The confirmed click did not run: ${done.result}`);
  await settled("send:click", 1);
  assert.equal(site.count("send:click"), 1, "The confirmed commit must click exactly once.");
  assert.deepEqual(site.state.orders, [{ name: "Mira", password: "" }], "The form must arrive with the typed name and no secret.");

  // Stop, while the Heinzel is at work: its next calls are refused and its tabs are gone.
  const second = turn("JOURNEY-TURN-2 Carry on.");
  await withTimeout((async () => { for (;;) { try { await access(join(root, "sync", "ready-for-stop")); return; } catch { await delay(200); } } })(),
    120_000, "The Heinzel never reached the point where the person stops it.");
  await settled("receipt:click", 1);
  assert.equal(site.count("receipt:click"), 1, "Before Stop the Heinzel still clicks.");
  await panel(page, { action: "stop-agent", tab: report.tab });
  await writeFile(join(root, "sync", "stopped"), "stopped\n", { mode: 0o600 });
  const after = (await second).report;
  assert.equal(after.stop_seen, true);
  assert.ok(ran(after.click_before_stop), after.click_before_stop);
  for (const step of ["click_after_stop", "read_after_stop", "open_after_stop"])
    assert.ok(refused(after[step]), `After Stop ${step} must be refused: ${after[step]}`);
  await settled("receipt:click", 1);
  assert.equal(site.count("receipt:click"), 1, "After Stop nothing is clicked.");
  assert.deepEqual(await agentTabs(page), [], "Stop must close every tab of the agent.");
}

/** A minimal MCP client over streamable HTTP: JSON or one event-stream answer per request. */
function mcpClient(url, token) {
  let id = 0, sessionId = null;
  const post = async (message) => {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json, text/event-stream",
        Authorization: `Bearer ${token}`, ...(sessionId ? { "Mcp-Session-Id": sessionId, "Mcp-Protocol-Version": "2025-06-18" } : {}) },
      body: JSON.stringify(message),
    });
    sessionId = response.headers.get("mcp-session-id") ?? sessionId;
    const text = await response.text();
    if (message.id === undefined) return null;
    assert.ok(response.ok, `MCP answered ${response.status}: ${text.slice(0, 300)}`);
    const frames = (response.headers.get("content-type") ?? "").includes("text/event-stream")
      ? text.split(/\r?\n/).filter((line) => line.startsWith("data:")).map((line) => JSON.parse(line.slice(5)))
      : [JSON.parse(text)];
    return frames.find((frame) => frame.id === message.id);
  };
  const request = (method, params) => post({ jsonrpc: "2.0", id: (id += 1), method, params });
  return {
    async start() {
      const hello = await request("initialize", { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "journey-agent", version: "0" } });
      assert.ok(hello?.result, `MCP did not initialize: ${JSON.stringify(hello)}`);
      await post({ jsonrpc: "2.0", method: "notifications/initialized" });
    },
    /** The tool's text, as `Error: ...` when the call is refused, so both journeys read one shape. */
    async call(name, args = {}) {
      const answer = await request("tools/call", { name: `ui_browser_${name}`, arguments: args });
      if (answer?.error) return `Error: ${answer.error.message}`;
      const text = (answer?.result?.content ?? []).map((part) => part.text ?? "").join("\n");
      return answer?.result?.isError && !text.startsWith("Not run:") ? `Error: ${text.replace(/^Error: /, "")}` : text;
    },
  };
}

async function mcpJourney(page, origin) {
  const before = await api(page, "GET", "/_web/mcp");
  assert.equal(before.status, 200, "This checkout has no /mcp on the web server (head F of ADR 0162): the outside-agent journey cannot run on it.");
  const url = `${origin}/mcp`;
  assert.equal((await fetch(url, { method: "POST", body: "{}" })).status, 404, "Off, /mcp answers 404.");
  assert.equal((await api(page, "PUT", "/_web/mcp", { body: { enabled: true } })).status, 200);
  const token = async (actor, scope) => {
    const made = await api(page, "POST", "/_web/mcp/tokens", { body: { actor, scope, days: 1 } });
    assert.equal(made.status, 200, `No ${scope} token: ${made.text}`);
    return made.json.token;
  };
  const reader = mcpClient(url, await token("journey-reader", "read"));
  const agent = mcpClient(url, await token("journey-agent", "write"));
  await agent.start();
  let tabs = "";
  for (let turn = 0; turn < 20 && !/"available": ?true/.test(tabs); turn += 1) { if (turn) await delay(1000); tabs = await agent.call("tabs"); }
  assert.match(tabs, /"available": ?true/, `The desktop's bridge never connected: ${tabs}`);
  assert.ok(refused(await agent.call("open", { url: NOT_PUBLIC[0] })), "A tab on a literal address must be refused.");
  const tab = tabOf(await agent.call("open", { url: SHOP_URL }));
  let read = await agent.call("read", { tab });
  assert.ok(read.includes("Add a gift note") && /\[ref_\d+\]/.test(read), `The page must be read with refs: ${read.slice(0, 300)}`);
  for (const address of NOT_PUBLIC) {
    const answer = await agent.call("navigate", { tab, url: address });
    assert.ok(refused(answer), `${address} must be refused: ${answer}`);
  }
  await reader.start();
  const unscoped = await reader.call("click", { tab, ref: refOf(read, "button", "Add a gift note") });
  assert.ok(refused(unscoped), `A read token must not click: ${unscoped}`);
  assert.equal(site.count("note:click"), 0);
  const expectRan = async (step, name, args) => { const answer = await agent.call(name, args); assert.ok(ran(answer), `${step} must run: ${answer}`); };
  const expectRefused = async (step, name, args) => { const answer = await agent.call(name, args); assert.ok(refused(answer), `${step} must be refused: ${answer}`); };
  await expectRan("Typing a name", "type", { tab, ref: refOf(read, "textbox", "Name"), text: "Mira" });
  await expectRan("An ordinary click", "click", { tab, ref: refOf(read, "button", "Add a gift note") });
  await expectRefused("A password field", "type", { tab, ref: refOf(read, "textbox", "Password"), text: "not-a-real-secret" });
  await expectRefused("A covered target", "click", { tab, ref: refOf(read, "button", "Show delivery times") });
  await expectRefused("A target swapped on the press", "click", { tab, ref: refOf(read, "button", "Show opening hours") });
  const cardTab = tabOf(await agent.call("open", { url: `${SHOP_URL}card` }));
  const card = await agent.call("read", { tab: cardTab });
  await expectRefused("A card field", "type", { tab: cardTab, ref: refOf(card, "textbox", "Card number"), text: "4111111111111111" });
  await agent.call("close", { tab: cardTab });
  await assertInputs(tab);
  const [shown] = await agentTabs(page);
  assert.equal(shown?.id, tab);
  assert.equal(shown.agent.kind, "external");
  assert.equal(shown.url, SHOP_URL, "No refused address may have moved the tab.");

  // The commit waits for the card on that tab: first the person says no, then yes.
  read = await agent.call("read", { tab });
  const send = { tab, ref: refOf(read, "button", "Send order") };
  const answerCard = async (action) => {
    const call = agent.call("click", send);
    await withTimeout((async () => { while (!(await agentTabs(page)).some((entry) => entry.id === tab && entry.commit)) await delay(200); })(),
      30_000, "The commit card never appeared on the agent's tab.");
    await delay(750);
    assert.deepEqual(site.state.orders, [], "While the card waits, nothing is sent.");
    assert.equal(site.count("send:click"), 0);
    await panel(page, { action, tab });
    return call;
  };
  const declined = await answerCard("commit-refuse");
  assert.ok(refused(declined) || proposed(declined), `A declined commit must not run: ${declined}`);
  await delay(750);
  assert.deepEqual(site.state.orders, [], "A declined commit sends nothing.");
  const approved = await answerCard("commit-approve");
  assert.ok(ran(approved), `The approved commit must run: ${approved}`);
  await settled("send:click", 1);
  assert.equal(site.count("send:click"), 1, "The confirmed commit must click exactly once.");
  assert.deepEqual(site.state.orders, [{ name: "Mira", password: "" }], "The form must arrive with the typed name and no secret.");

  // Stop: the agent's calls are refused and its tabs are gone.
  read = await agent.call("read", { tab });
  const receipt = { tab, ref: refOf(read, "button", "Show receipt") };
  await expectRan("A click before Stop", "click", receipt);
  await settled("receipt:click", 1);
  await panel(page, { action: "stop-agent", tab });
  await expectRefused("A click after Stop", "click", receipt);
  await expectRefused("A read after Stop", "read", { tab });
  await expectRefused("A new tab after Stop", "open", { url: SHOP_URL });
  await settled("receipt:click", 1);
  assert.equal(site.count("receipt:click"), 1, "After Stop nothing is clicked.");
  assert.deepEqual(await agentTabs(page), [], "Stop must close every tab of the agent.");
}

try {
  await Promise.all(dirs.map((name) => mkdir(join(root, name), { mode: 0o700 })));
  site = await startSite(join(root, "site"));
  // `hz` on the app's PATH: this checkout's CLI, with the journey's scripted runner added (browser_journey_hz.py).
  const helper = fileURLToPath(new URL("./browser_journey_hz.py", import.meta.url));
  await writeFile(join(root, "bin", "hz"),
    `#!${python} -I\nimport runpy\nrunpy.run_path(${JSON.stringify(helper)}, run_name="__main__")\n`, { mode: 0o700 });
  await writeFile(join(root, "config", "config.toml"), '[hz]\ndisabled_modules = ["tailscale", "clock"]\nupdate_check = false\n', { mode: 0o600 });
  const env = {
    HOME: join(root, "home"), TMPDIR: join(root, "tmp"), PATH: `${join(root, "bin")}:/usr/bin:/bin:/usr/sbin:/sbin`,
    USER: process.env.USER || "", LOGNAME: process.env.LOGNAME || "", LANG: "en_US.UTF-8",
    ...(process.env.DISPLAY ? { DISPLAY: process.env.DISPLAY } : {}),
    XDG_CONFIG_HOME: join(root, "config"), XDG_DATA_HOME: join(root, "data"), XDG_STATE_HOME: join(root, "state"),
    XDG_CACHE_HOME: join(root, "cache"), HZ_CONFIG_HOME: join(root, "config"), HZ_DATA_DIR: join(root, "data"),
    HZ_STATE_DIR: join(root, "state"), HZ_CACHE_DIR: join(root, "cache"), HZ_LOG_DIR: join(root, "logs"),
    HZ_NATIVE_TEST_ROOT: root, HZ_CAPABILITIES__AGENT_RUNNER: "journey",
    HZ_JOURNEY_PROXY: String(site.proxyPort), HZ_JOURNEY_CERT: site.certificate, HZ_JOURNEY_HOSTS: `${SHOP},${WIDGET}`,
    HZ_JOURNEY_SHOP: SHOP, HZ_JOURNEY_SYNC: join(root, "sync"),
  };
  launchAttempted = true;
  app = await electron.launch({
    executablePath: electronExecutable,
    args: [fileURLToPath(new URL("./browser-journey-bootstrap.mjs", import.meta.url))],
    cwd: root, env, timeout: 60_000,
  });
  electronProcess = app.process();
  assert.equal(await app.evaluate(({ app: current }) => current.getPath("userData")), join(root, "electron-profile"));
  const page = await app.firstWindow({ timeout: 90_000 });
  await page.waitForFunction(() => Boolean(sessionStorage.getItem("hz.token")) && typeof window.heinzelDesktop?.browser === "function",
    null, { polling: 250, timeout: 60_000 });
  ownedPid = Number(await readFile(join(root, "owned-pid"), "utf8"));
  const origin = new URL(page.url()).origin;
  assert.equal(new URL(origin).hostname, "127.0.0.1");
  assert.equal(await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().some((window) => window.isVisible())), false,
    "The journey's app must stay hidden.");
  if (journey === "heinzel") await heinzelJourney(page, origin);
  else await mcpJourney(page, origin);
  assertNothingElseDialled();
  assert.equal(await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().some((window) => window.isVisible())), false,
    "The journey's app must stay hidden.");
  const exited = new Promise((done) => electronProcess.once("exit", (code, signal) => done({ code, signal })));
  await page.close();
  const result = await withTimeout(exited, 20_000, "Electron did not exit after close.");
  stopped = true;
  assert.equal(result.code, 0, `Electron closed abnormally: ${JSON.stringify(result)}`);
  console.log(`Browser journey "${journey}" passed: read with refs, typed, clicked, the commit waited for the person, ` +
    "swapped targets, secret fields and non-public addresses were refused, and Stop ended the agent's actions.");
} finally {
  site?.close();
  await cleanupHarness({
    app, electronProcess, stopped, root, ownedPid, launchAttempted,
    loadPid: async () => Number(await readFile(join(root, "owned-pid"), "utf8")),
    processExists: (pid) => { try { process.kill(pid, 0); return true; } catch (error) { if (error.code === "ESRCH") return false; throw error; } },
    remove: (path) => rm(path, { recursive: true, force: true }),
  });
}
