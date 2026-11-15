// The browser journey (ADR 0162, the phase-1 release gate): an agent drives the built-in browser of the real app.
//   node e2e/browser-journey.mjs heinzel   a chat turn on a scripted model that calls hz's tools directly
//   node e2e/browser-journey.mjs mcp       an outside agent over /mcp on the web server, with a write token
// Real Electron, real `hz ui serve`, real bridge and guests; the app stays hidden, and the only site is a fixture on
// loopback served under two made-up public names (browser-journey-site.mjs). HZ_JOURNEY_PYTHON is the Python of
// this checkout's environment (`.venv/bin/python`).
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { constants } from "node:fs";
import { access, appendFile, mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, isAbsolute, join, relative, resolve } from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { fileURLToPath } from "node:url";
import { _electron as electron } from "../../web/node_modules/playwright-core/index.mjs";
import { cleanupHarness, withTimeout } from "./harness-cleanup.mjs";
import { RACE_TRIES, RACE_WAITS, SEAM_EVERY, SHOP, WIDGET, startSite } from "./browser-journey-site.mjs";

const journey = process.argv[2];
// Under the virtual display each journey notes how many raced presses landed inside the other site's frame; `tally`
// then requires at least one across both. Which try wins the race is a matter of milliseconds, not of the journey.
const tallyFile = process.env.HZ_JOURNEY_TALLY;
if (journey === "tally") {
  if (process.env.HZ_JOURNEY_VISIBLE !== "1") { console.log("Browser journeys: a press inside the other site's frame is only required under the virtual display."); process.exit(0); }
  if (!tallyFile) throw new Error("Set HZ_JOURNEY_TALLY to the file the journeys note their in-frame presses in.");
  const lines = (await readFile(tallyFile, "utf8").catch(() => "")).trim().split("\n").filter(Boolean);
  const total = lines.reduce((sum, line) => sum + Number(line.split(" ")[1]), 0);
  console.log(`Browser journeys: presses inside the other site's frame: ${lines.join(", ") || "none noted"}.`);
  assert.deepEqual(lines.map((line) => line.split(" ")[0]).sort(), ["heinzel", "mcp"], "Both journeys must have noted their count.");
  assert.ok(total > 0, "Across both journeys at least one raced press must land inside the other site's frame, or nothing is proven about it.");
  process.exit(0);
}
// HZ_JOURNEY_VISIBLE=1, on Linux under a virtual display only (Xvfb, see browser-journey.Dockerfile): the window is
// really shown, and then at least one raced press must land inside the frame of the other site's process.
const visible = process.env.HZ_JOURNEY_VISIBLE === "1";
if (visible && process.platform !== "linux") throw new Error("A shown window is for Linux under Xvfb only; everywhere else the app stays hidden.");
if (visible && !tallyFile) throw new Error("Set HZ_JOURNEY_TALLY to the file the journeys note their in-frame presses in.");
// The Heinzel's journey is the first of a run: it starts the notes afresh, so no earlier run's count is added.
if (visible && journey === "heinzel") await writeFile(tallyFile, "");
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
const dirs = ["bin", "home", "config", "data", "state", "cache", "logs", "electron-profile", "electron-session", "electron-logs", "electron-crashes", "tmp", "sync", "site"];
let app, electronProcess, ownedPid, site;
let stopped = false, launchAttempted = false;
// What the run found, for its last line: the tab's viewport with the panel closed and open, and the race's outcome.
// Diagnostic: the answers of the outside agent's first calls, printed when a journey fails.
const trace = [];
const found = { closed: null, shown: null, raceShown: 0, raceInFrame: 0, raceClicks: 0, strayClicks: 0, strayTargets: [], personClicks: 0, personPaused: null, pointClicks: 0, seam: [] };

// Why a call was refused, as the product words it; a refusal for any other reason (an unknown ref or tab) is a failure.
const REASONS = {
  address: /public https:\/\/ addresses only|Only public HTTPS addresses/,
  secret: /password, one-time-code or payment-card field|Only the person may type here/,
  covered: /Something else covers that element/,
  swapped: /put something else under the pointer/,
  stopped: /stopped this agent/,
  changed: /page changed/i,
  // The engine pressed and released, and then could not confirm that its target got the click.
  unconfirmed: /Not confirmed: the page changed under the pointer and the click may not have reached its target\./,
  scope: /\bwrite\b/i,
};
/** How long the agent's next call may take after the page opened its own dialogs. */
const AFTER_DIALOG_SECONDS = 10;
/**
 * The page's dialogs opened on the agent's click, the page went on, and the click and the agent's next call were
 * answered in time. Nobody was asked, so the confirm was answered no and the prompt with nothing: a yes here would be
 * an "Are you sure?" that an agent's click agreed to.
 */
function assertDialogs(click, next, [clickSeconds, nextSeconds]) {
  assert.ok(ran(click), `The click that makes the page open its dialogs must run: ${click}`);
  assert.equal(site.count("dialog:click"), 1, "The button that opens the dialogs must be clicked once.");
  assert.equal(site.starting("dialog:done:"), 1, "The page must get past its alert, its confirm and its prompt: none may leave it waiting.");
  // alert() returns nothing, confirm() says no, and prompt() gives nothing or is not there at all (Electron throws).
  const [dialogs] = site.state.events.filter((event) => event.startsWith("dialog:done:"));
  assert.match(dialogs, /^dialog:done:undefined:false:(null|thrown)$/, `The page's alert must return, its confirm must be answered no and its prompt with nothing: ${dialogs}`);
  assert.ok(clickSeconds < AFTER_DIALOG_SECONDS, `The click that opened the page's dialogs took ${clickSeconds} s.`);
  assert.ok(ran(next), `After the page's dialogs the agent's next call must be answered: ${next}`);
  assert.ok(nextSeconds < AFTER_DIALOG_SECONDS, `After the page's dialogs the agent's next call took ${nextSeconds} s.`);
}
const FRAME_TAGS = ["IFRAME", "FRAME", "OBJECT", "EMBED", "none"];
/**
 * Where the release of a called-off or unconfirmed press may be heard as a stray mouseup or click: on the page's root
 * only. The engine holds back every click that does not land on its target; below the root a click could act (a label
 * clicks its control, a summary toggles, a row may carry a handler), and a frame must never hear it.
 */
const strayAllowed = (tag) => (tag === "HTML" || tag === "BODY") && !FRAME_TAGS.includes(tag);
const RACE_ZONES = ["race-a", "race-b"];
/** What the page has heard so far, to tell what one try added. */
const heard = () => ({
  clicks: Object.fromEntries(RACE_ZONES.map((zone) => [zone, site.count(`${zone}:click`)])),
  strays: site.starting("stray:click:"), orders: site.state.orders.length, send: site.starting("send:"),
  inFrame: site.countOf("widget-race-", "pointerdown"),
});
/** The same, with how often the seam has held a press so far: the snapshot taken around every raced try. */
const note = async () => ({ ...heard(), seam: (await kept("seam")).split("\n").filter(Boolean).length });
// A raced try takes its turn slowly enough that an agent's calls stay under a minute's limit, however many tries.
const TRY_PAUSE = 1300;
/** For the run's last line: what each try ended as, by the page's wait. C clicked, c covered, x called off, u not confirmed, F pressed in the frame; S marks a try whose press the seam held. */
const raceLine = [];
/**
 * One raced try against what it added to the page. Answered as clicked: exactly one click, on its own button, and no
 * stray one. Any other answer: no button was clicked, and a stray click only if that same answer says the click was
 * called off or not confirmed. Whatever the answer: no order was sent and nothing reached the commit's button.
 */
function checkTry(attempt, answer, before, after) {
  const zone = RACE_ZONES[attempt % 2], other = RACE_ZONES[(attempt + 1) % 2];
  const clicks = after.clicks[zone] - before.clicks[zone], strays = after.strays - before.strays;
  assert.equal(after.clicks[other] - before.clicks[other], 0, `Try ${attempt + 1} must not click the other button.`);
  assert.deepEqual([after.orders, after.send], [0, 0], `Try ${attempt + 1} must send no order and touch no commit button.`);
  // A press that landed inside the other site's frame can never be answered as clicked.
  const inFrame = after.inFrame - before.inFrame;
  assert.ok(inFrame === 0 || !ran(answer), `Try ${attempt + 1} pressed inside the frame and was answered as clicked: ${answer}`);
  // A press that the seam held until the other site's frame was drawn can never be answered as clicked either.
  const held = after.seam - before.seam;
  assert.ok(held <= 1, `Try ${attempt + 1} had its press held ${held} times.`);
  assert.ok(held === 0 || !ran(answer), `Try ${attempt + 1} pressed with the other site's frame drawn over its button and was answered as clicked: ${answer}`);
  raceLine.push(`${held ? "S" : ""}${RACE_WAITS[attempt]}${inFrame ? "F" : ran(answer) ? "C" : refused(answer, "covered") ? "c" : refused(answer, "unconfirmed") ? "u" : "x"}`);
  if (ran(answer)) assert.deepEqual({ clicks, strays }, { clicks: 1, strays: 0 }, `Try ${attempt + 1} was answered as clicked: ${answer}`);
  else {
    assert.equal(clicks, 0, `Try ${attempt + 1} was not answered as clicked, so its button must not be clicked: ${answer}`);
    assert.ok(strays === 0 || (strays === 1 && notClicked(answer)), `Try ${attempt + 1} left ${strays} stray clicks on the answer: ${answer}`);
  }
}
/**
 * The person's own click in the agent's tab: real input to the guest from the app, not from the agent's engine. It
 * goes to the middle of the element; nothing the engine armed for its own press may hold it back.
 */
const personClick = (selector) => withTimeout(app.evaluate(async ({ webContents }, { url, selector }) => {
  const guest = webContents.getAllWebContents().find((contents) => !contents.isDestroyed() && contents.getURL() === url);
  const [x, y] = await guest.executeJavaScript(`(() => { const box = document.querySelector(${JSON.stringify(selector)}).getBoundingClientRect();
    return [Math.round(box.left + box.width / 2), Math.round(box.top + box.height / 2)]; })()`);
  guest.sendInputEvent({ type: "mouseMove", x, y });
  guest.sendInputEvent({ type: "mouseDown", x, y, button: "left", clickCount: 1 });
  guest.sendInputEvent({ type: "mouseUp", x, y, button: "left", clickCount: 1 });
}, { url: SHOP_URL, selector }), 15_000, "The guest did not take the person's click.");
/** The form as the page holds it, read directly: a stray click must have changed nothing in it and moved no page. */
const formState = () => withTimeout(app.evaluate(async ({ webContents }, url) => {
  const guest = webContents.getAllWebContents().find((contents) => !contents.isDestroyed() && contents.getURL() === url);
  return guest ? guest.executeJavaScript(`({ name: document.querySelector("[name=name]").value, password: document.querySelector("[name=password]").value,
    path: location.pathname, banner: document.getElementById("banner").classList.contains("swapped") })`) : null;
}, SHOP_URL), 15_000, "The guest did not answer.");
const refused = (text, reason) => typeof text === "string" && text.startsWith("Error:") && (!reason || REASONS[reason].test(text));
/** The submit that was sent and replaced the page before anything could confirm it: `done` false, `navigated` true, and the order's page named as where it went. */
const wentOn = (text) => typeof text === "string" && !refused(text) && !proposed(text) && /"done": ?false/.test(text) && /"navigated": ?true/.test(text) &&
  text.includes(`${SHOP_URL}order`);
/** The answer of a click that was pressed and then called off or not confirmed: the only tries that may leave a stray click. */
const notClicked = (text) => refused(text, "swapped") || refused(text, "unconfirmed");
const proposed = (text) => typeof text === "string" && text.startsWith("Not run:");
// Done, and said so: an input that was only sent, with nothing to say it arrived, answers `done: false` and is not this.
const ran = (text) => typeof text === "string" && !refused(text) && !proposed(text) && /t-[0-9a-f]{8}/.test(text) && !/"done": ?false/.test(text);
const tabOf = (text) => /t-[0-9a-f]{8}/.exec(text)?.[0] ?? "";
function refOf(text, role, name) {
  const ref = new RegExp(`- ${role} \\\\?"${name}\\\\?"[^\\[]{0,120}\\[(ref_\\d+)\\]`).exec(text)?.[1];
  assert.ok(ref, `The page that was read has no ${role} named "${name}": ${String(text).slice(0, 300)}`);
  return ref;
}
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
const panel = (page, request) => withTimeout(page.evaluate((command) => window.heinzelDesktop.browser(command), request),
  30_000, `The browser panel did not answer ${request.action}.`);
const agentTabs = async (page) => (await panel(page, { action: "tabs" })).tabs.filter((tab) => tab.agent);
const kept = async (name) => { try { return await readFile(join(root, name), "utf8"); } catch { return ""; } };
const signal = (name) => writeFile(join(root, "sync", name), "done\n", { mode: 0o600 });
/** Wait until the scripted model says it is at `name`; a turn that ended before that fails with its own reason. */
async function signalled(name, message, running) {
  let over = false;
  running.then(() => { over = true; }, () => { over = true; });
  for (let turn = 0; turn < 600 && !over; turn += 1) {
    try { await access(join(root, "sync", name)); return; } catch { await delay(200); }
  }
  await running;
  assert.fail(message);
}
const PANEL = { x: 300, y: 80, width: 760, height: 640 };
const viewportOf = async () => (await guestFacts()).find((fact) => fact.url === SHOP_URL)?.page?.inner ?? null;
/** An agent's tab that was never shown must have a real size all the same: agents work with the panel closed. */
async function backgroundSize() {
  found.closed = await viewportOf();
  assert.ok(found.closed?.[0] > 0 && found.closed[1] > 0, `A never-shown agent tab must have a size: ${JSON.stringify(found.closed)}`);
}
/**
 * The person opens the browser panel and selects the agent's tab. Until then the tab's view was never visible; what
 * size the page had before and has after is kept for the run's last line.
 */
async function showPanel(page, tab) {
  await backgroundSize();
  await panel(page, { action: "select-tab", tab });
  await panel(page, { action: "layout", bounds: PANEL });
  // Shown, the page has the view's own size: the size it was given while hidden must be gone.
  for (let turn = 0; turn < 20; turn += 1) {
    found.shown = await viewportOf();
    if (found.shown?.[0] === PANEL.width && found.shown[1] === PANEL.height) return;
    await delay(250);
  }
  assert.fail(`The selected agent tab must have the panel's size (${PANEL.width} by ${PANEL.height}): ${JSON.stringify(found)}`);
}

/** For a failure report only: every guest page of the shop, asked directly from the main process. */
function guestFacts() {
  return withTimeout(app.evaluate(async ({ webContents }, host) => {
    const facts = [];
    for (const contents of webContents.getAllWebContents()) {
      if (contents.isDestroyed() || !contents.getURL().includes(host)) continue;
      const fact = { url: contents.getURL(), attached: contents.debugger.isAttached() };
      try {
        fact.page = await contents.executeJavaScript(`(() => {
          const button = document.querySelector("[data-watch=note]");
          const box = button ? button.getBoundingClientRect() : null;
          const hit = box ? document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2) : null;
          return { visibility: document.visibilityState, inner: [innerWidth, innerHeight], ratio: devicePixelRatio,
            box: box ? [box.left, box.top, box.width, box.height] : null, hit: hit ? hit.tagName : null,
            frames: document.querySelectorAll("iframe").length };
        })()`);
        const [left, top, width, height] = fact.page.box ?? [0, 0, 0, 0];
        fact.node = await contents.debugger.sendCommand("DOM.getNodeForLocation",
          { x: Math.round(left + width / 2), y: Math.round(top + height / 2), includeUserAgentShadowDOM: false, ignorePointerEventsNone: true });
      } catch (error) { fact.error = String(error?.message ?? error); }
      facts.push(fact);
    }
    return facts;
  }, SHOP), 15_000, "The guest did not answer.");
}

/** What both journeys must have left behind once the agent did the free and the forbidden steps. */
async function assertInputs(tab, race) {
  await settled("press:mousedown", 1);
  assert.equal(site.count("note:click"), 1 + found.personClicks + found.pointClicks, "The ordinary button must be clicked once by the agent by ref, and once more by the person and by the agent at a point where the journey has them do so.");
  for (const [zone, shown] of [["hover", "hover:swapped"], ["press", "press:swapped"], ["race-a", "race-a:shown"], ["race-b", "race-b:shown"]]) {
    assert.equal(site.count(`widget-${zone}:ready`), 1, `The ${zone} frame must be loaded, with its listeners.`);
    const first = site.state.events.indexOf(shown);
    assert.ok(first < 0 || site.state.events.indexOf(`widget-${zone}:ready`) < first, `The ${zone} frame must have been listening before the page showed it.`);
  }
  // The race: the page showed a frame of another site a moment after the pointer arrived. A try either clicked the
  // button before the frame was there, or was refused; a press that landed in the frame must end without a release
  // or a click there.
  for (const answer of race)
    assert.ok(ran(answer) || notClicked(answer) || refused(answer, "covered") || refused(answer, "changed"), `A raced click must run or be refused as changed: ${answer}`);
  assert.equal(race.length, RACE_TRIES, "Every raced try must have been made.");
  found.raceShown = site.count("race-a:shown") + site.count("race-b:shown");
  found.raceInFrame = site.countOf("widget-race-", "pointerdown");
  found.raceClicks = site.count("race-a:click") + site.count("race-b:click");
  assert.equal(race.filter(ran).length, found.raceClicks, "Every raced click that ran, and no other, clicked its button.");
  // The seam's tries: the engine's own press was held until the page had shown and drawn its frame of the other site.
  const seamed = (await kept("seam")).trim().split("\n").filter(Boolean).map((line) => JSON.parse(line));
  found.seam = seamed.map((use) => `${use.zone} ${use.waited} ms`);
  assert.ok(seamed.length > 0 && seamed.length <= Math.ceil(RACE_TRIES / SEAM_EVERY), `The seam must have held the press of tries that are its own, and of no other: ${found.seam.join(", ")}`);
  // Under the virtual display those presses land inside the frame, in each journey; `tally` adds the two up. In a
  // window that is never shown they reach the frame's element in this document instead. Either way the frame got no
  // release and no click, which is checked right here for every try.
  if (visible) assert.ok(found.raceInFrame > 0, `With the frame drawn before the press, at least one press must land inside it: ${found.seam.join(", ")}`);
  // What a called-off press must never do (ADR 0162, safety 3 and 5.5):
  // 1. The frame of the other site gets no release and no click, whichever try put a press into it.
  assert.equal(site.countOf("widget-", "mouseup") + site.countOf("widget-", "click"), 0,
    "No frame of the other site may get a release or a click.");
  // 2. The agent's own target gets no release and no click: a raced button is released only when it is clicked.
  for (const zone of ["race-a", "race-b"])
    assert.equal(site.count(`${zone}:mouseup`), site.count(`${zone}:click`), `A called-off press must not be released on its button (${zone}).`);
  // 3. The button is let go outside the page, so the page's own document may hear that release as a click on what
  // lies around the button. That is the page's own event in its own document, and it reaches nothing of another
  // site. It is allowed only as that: at most one per called-off press, never on a button and never on a frame.
  const calledOff = 2 + race.filter(notClicked).length;
  found.strayClicks = site.starting("stray:click:");
  assert.ok(found.strayClicks <= calledOff, `At most one stray click per called-off press: ${found.strayClicks} for ${calledOff}.`);
  assert.ok(site.starting("stray:mouseup:") <= calledOff && found.strayClicks <= site.starting("stray:mouseup:"),
    "A stray click comes only from the release of a called-off press, at most one each.");
  // It may hit the page's root only, never an element below it and never a frame.
  found.strayTargets = [...new Set(site.state.events.filter((event) => /^stray:(mouseup|click):/.test(event)).map((event) => event.split(":")[2]))];
  assert.deepEqual(found.strayTargets.filter((tag) => !strayAllowed(tag)), [],
    `A stray release or click may hit the page's root only: ${found.strayTargets}`);
  // Next to the stray clicks: nothing was sent, nothing reached the commit's button, the page did not move, and the
  // form holds what was typed and nothing else.
  assert.equal(site.starting("send:"), 0, "Nothing may have reached the commit's button.");
  assert.deepEqual(await formState(), { name: "Mira", password: "", path: "/", banner: true }, "The form and the page must be as the agent left them.");
  assert.equal(site.count("document:click"), 2 + found.personClicks + found.pointClicks + found.raceClicks + found.strayClicks, "Every click in the page is one of the two ordinary ones, the person's own, a raced button's own or a stray one.");
  assert.equal(site.count("document:mousedown"), 4 + found.personClicks + found.pointClicks + site.count("race-a:mousedown") + site.count("race-b:mousedown") + site.starting("stray:mousedown:"),
"Every press in the page is the ordinary click's, the two answered with a cover, a raced button's own or one on the frame element.");
  assert.ok(site.starting("stray:mousedown:") <= race.filter((answer) => refused(answer, "swapped")).length, "A press beside every button belongs to a raced try that was called off.");
  assert.equal(site.count("hover:swapped"), 1, "The page must have slid its frame in when the pointer moved.");
  assert.equal(site.count("hover:mousedown") + site.count("hover:click"), 0, "Nothing may be pressed once a frame covers the target.");
  assert.equal(site.starting("widget-hover:") - site.count("widget-hover:ready"), 0, "The covering frame must get no press at all.");
  // The same on an ordinary page: a banner of the page's own covered the button on the press.
  assert.equal(site.count("banner:swapped"), 1, "The page must have shown its banner on the press.");
  assert.equal(site.count("banner:mousedown"), 1, "The press must have reached the button under the banner.");
  assert.equal(site.count("banner:mouseup") + site.count("banner:click"), 0, "A press that the page answered with its banner must not be released or clicked on the button.");
  assert.equal(site.count("press:swapped"), 1, "The page must have slid its frame in on the press.");
  assert.equal(site.count("press:mousedown"), 1, "The press must have reached the described button.");
  assert.equal(site.count("press:click"), 0, "A press that the page answered with a swap must not become a click.");
  assert.equal(site.count("press:mouseup"), 0, "The release must not land on the button.");
  assert.equal(site.starting("widget-press:") - site.count("widget-press:ready"), 0, "The frame shown on the press must get nothing: no press, no release, no click.");
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

  const running = turn("JOURNEY-TURN-1 Please send the tea order for Mira.");
  running.catch(() => {});  // reported below, by the await
  await signalled("tab-open", "The Heinzel never opened its tab.", running);
  const [opened1] = await agentTabs(page);
  assert.ok(opened1, "The Heinzel's tab must be in the panel's strip.");
  // The Heinzel works in the background: the person never opens the panel in this journey.
  await backgroundSize();
  await signal("panel-shown");
  // After every raced try the scripted model stops until the journey has noted what the page heard.
  const notes = [];
  for (let mark = 0; mark <= RACE_TRIES; mark += 1) {
    await signalled(`try-${mark}`, `The Heinzel never reached raced try ${mark}.`, running);
    await delay(TRY_PAUSE);
    notes.push(await note());
    await signal(`try-${mark}-seen`);
  }
  const first = await running;
  const report = first.report;
  assert.match(report.tabs, /"available": ?true/, `The desktop's bridge never connected: ${report.tabs}`);
  assert.ok(refused(report.open_not_public, "address"), `A tab on a literal address must be refused: ${report.open_not_public}`);
  assert.ok(report.read.includes("Add a gift note") && /\[ref_\d+\]/.test(report.read), `The page must be read with refs: ${report.read}`);
  for (const address of NOT_PUBLIC) assert.ok(refused(report.not_public[address], "address"), `${address} must be refused: ${report.not_public[address]}`);
  assert.ok(ran(report.type_name), `Typing a name must run: ${report.type_name}`);
  assert.ok(ran(report.click_note), `An ordinary click must run: ${report.click_note}`);
  assert.ok(refused(report.type_password, "secret"), `A password field must be refused: ${report.type_password}`);
  assert.ok(refused(report.type_card, "secret"), `A card field must be refused: ${report.type_card}`);
  assert.ok(refused(report.click_hover_swap, "covered"), `A covered target must be refused: ${report.click_hover_swap}`);
  assert.ok(refused(report.click_press_swap, "swapped"), `A target swapped on the press must be refused: ${report.click_press_swap}`);
  assert.ok(notClicked(report.click_banner), `A target covered by the page's own banner on the press must not be answered as clicked: ${report.click_banner}`);
  report.race.forEach((answer, attempt) => checkTry(attempt, answer, notes[attempt], notes[attempt + 1]));
  assertDialogs(report.click_dialog, report.after_dialog, report.dialog_seconds);
  assert.ok(proposed(report.send), `Sending the form is a commit and must be proposed: ${report.send}`);
  await assertInputs(report.tab, report.race);
  // The card's tab was closed on the agent's word, leave prompt or not: only the shop's tab is left.
  assert.ok(ran(report.close_card) && report.close_card.includes(report.card_tab), `Closing the card's tab must run: ${report.close_card}`);
  const left = await agentTabs(page);
  assert.deepEqual(left.map((entry) => entry.id), [report.tab], "The card's tab must be gone and the shop's tab must stay.");
  const [tab] = left;
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
  assert.equal(site.starting("stray:click:"), found.strayClicks, "The confirmed click adds no stray one.");
  assert.equal(site.count("document:click"), 3 + found.personClicks + found.pointClicks + found.raceClicks + found.strayClicks, "No click besides the ordinary one, the raced buttons' own, the called-off presses' stray ones and the confirmed one.");
  assert.deepEqual(site.state.orders, [{ name: "Mira", password: "" }], "The form must arrive with the typed name and no secret.");

  // Stop, while the Heinzel is at work: its next calls are refused and its tabs are gone.
  const second = turn("JOURNEY-TURN-2 Carry on.");
  second.catch(() => {});  // reported below, by the await
  await signalled("ready-for-stop", "The Heinzel never reached the point where the person stops it.", second);
  await settled("receipt:click", 1);
  assert.equal(site.count("receipt:click"), 1, "Before Stop the Heinzel still clicks.");
  await panel(page, { action: "stop-agent", tab: report.tab });
  await signal("stopped");
  const after = (await second).report;
  assert.equal(after.stop_seen, true);
  assert.ok(ran(after.click_before_stop), after.click_before_stop);
  for (const step of ["click_after_stop", "read_after_stop", "open_after_stop"])
    assert.ok(refused(after[step], "stopped"), `After Stop ${step} must be refused as stopped: ${after[step]}`);
  await settled("receipt:click", 1);
  assert.equal(site.count("receipt:click"), 1, "After Stop nothing is clicked.");
  assert.equal(site.count("document:click"), 4 + found.personClicks + found.pointClicks + found.raceClicks + found.strayClicks, "No click after Stop.");
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
  assert.ok(refused(await agent.call("open", { url: NOT_PUBLIC[0] }), "address"), "A tab on a literal address must be refused.");
  const openBegan = Date.now();
  const opened = await agent.call("open", { url: SHOP_URL });
  // A new tab is prepared on an empty page before it loads its address: that must answer at once, not after a time
  // limit, and Back from the first page must not lead to the empty one.
  assert.ok(Date.now() - openBegan < 5000, `Opening a tab on the shop took ${Date.now() - openBegan} ms.`);
  const back = await agent.call("navigate", { tab: tabOf(opened), to: "back" });
  assert.ok(ran(back) && back.includes(SHOP_URL) && !back.includes("about:blank"), `Back from a tab's first page must stay on it: ${back}`);
  trace.push({ tabs: tabs.slice(0, 400), open: opened.slice(0, 400) });
  const tab = tabOf(opened);
  // The outside agent works in sight: the person opens the panel on its tab.
  await showPanel(page, tab);
  let read = await agent.call("read", { tab });
  assert.ok(read.includes("Add a gift note") && /\[ref_\d+\]/.test(read), `The page must be read with refs: ${read.slice(0, 300)}`);
  for (const address of NOT_PUBLIC) {
    const answer = await agent.call("navigate", { tab, url: address });
    assert.ok(refused(answer, "address"), `${address} must be refused: ${answer}`);
  }
  await reader.start();
  const unscoped = await reader.call("click", { tab, ref: refOf(read, "button", "Add a gift note") });
  assert.ok(refused(unscoped, "scope"), `A read token must not click: ${unscoped}`);
  assert.equal(site.count("note:click"), 0);
  const expectRan = async (step, name, args) => { const answer = await agent.call(name, args); assert.ok(ran(answer), `${step} must run: ${answer}`); };
  const expectRefused = async (step, reason, name, args) => { const answer = await agent.call(name, args); assert.ok(refused(answer, reason), `${step} must be refused (${reason}): ${answer}`); };
  await expectRan("Typing a name", "type", { tab, ref: refOf(read, "textbox", "Name"), text: "Mira" });
  await expectRan("An ordinary click", "click", { tab, ref: refOf(read, "button", "Add a gift note") });
  await expectRefused("A password field", "secret", "type", { tab, ref: refOf(read, "textbox", "Password"), text: "not-a-real-secret" });
  await expectRefused("A covered target", "covered", "click", { tab, ref: refOf(read, "button", "Show delivery times") });
  await expectRefused("A target swapped on the press", "swapped", "click", { tab, ref: refOf(read, "button", "Show opening hours") });
  const banner = await agent.call("click", { tab, ref: refOf(read, "button", "Show the menu") });
  assert.ok(notClicked(banner), `A target covered by the page's own banner on the press must not be answered as clicked: ${banner}`);
  await delay(800);
  // Right after that called-off press the person clicks a button themselves: their click must go through, whatever
  // the engine armed to hold its own click back.
  await personClick("[data-watch=note]");
  found.personClicks = 1;
  await settled("note:click", 2);
  assert.equal(site.count("note:click"), 2, "The person's own click after a called-off press must reach its button.");
  // Their input in an agent's tab takes it over; they hand it back so the agent can go on.
  found.personPaused = (await agentTabs(page)).find((entry) => entry.id === tab)?.paused ?? null;
  if (found.personPaused) await panel(page, { action: "hand-back", tab });
  // Handed back, and on a scrolled page, the agent can click again: by a point here, by refs in the race below.
  const scrolled = await agent.call("scroll", { tab, direction: "down", amount: 1 });
  assert.ok(ran(scrolled), `Scrolling after the hand-back must run: ${scrolled}`);
  await delay(500);
  const at = await withTimeout(app.evaluate(async ({ webContents }, url) => {
    const guest = webContents.getAllWebContents().find((contents) => !contents.isDestroyed() && contents.getURL() === url);
    return guest.executeJavaScript(`(() => { const box = document.querySelector("[data-watch=note]").getBoundingClientRect();
      return { x: Math.round(box.left + box.width / 2), y: Math.round(box.top + box.height / 2), scrollY: Math.round(scrollY), height: innerHeight }; })()`);
  }, SHOP_URL), 15_000, "The guest did not answer.");
  assert.ok(at.scrollY > 0 && at.y > 0 && at.y < at.height, `The page must be scrolled with the button still in view: ${JSON.stringify(at)}`);
  const pointed = await agent.call("click", { tab, x: at.x, y: at.y });
  assert.ok(ran(pointed), `A click at a point on a scrolled page, after the hand-back, must run: ${pointed}`);
  found.pointClicks = 1;
  await settled("note:click", 3);
  assert.equal(site.count("note:click"), 3, "The click at a point must reach the button that is there.");
  const race = [];
  for (let attempt = 0; attempt < RACE_TRIES; attempt += 1) {
    const before = await note();
    const answer = await agent.call("click", { tab, ref: refOf(read, "button", attempt % 2 ? "Show prices tomorrow" : "Show prices today") });
    race.push(answer);
    await delay(TRY_PAUSE);  // the page takes its frame away again, and the try's events are in
    checkTry(attempt, answer, before, await note());
  }
  // The page opens an alert and a confirm on this click; the next call must still be answered, and soon.
  const began = Date.now();
  const dialogClick = await agent.call("click", { tab, ref: refOf(read, "button", "Show a notice") });
  const clicked = Date.now();
  const afterDialog = await agent.call("text", { tab });
  await delay(1000);  // the page's report is in
  assertDialogs(dialogClick, afterDialog, [(clicked - began) / 1000, (Date.now() - 1000 - clicked) / 1000]);
  const cardTab = tabOf(await agent.call("open", { url: `${SHOP_URL}card` }));
  const card = await agent.call("read", { tab: cardTab });
  await expectRefused("A card field", "secret", "type", { tab: cardTab, ref: refOf(card, "textbox", "Card number"), text: "4111111111111111" });
  const closed = await agent.call("close", { tab: cardTab });
  assert.ok(ran(closed) && closed.includes(cardTab), `Closing the card's tab must run: ${closed}`);
  assert.deepEqual((await agentTabs(page)).map((entry) => entry.id), [tab], "The card's tab must be gone and the shop's tab must stay.");
  await assertInputs(tab, race);
  const [shown] = await agentTabs(page);
  assert.equal(shown?.id, tab);
  assert.equal(shown.agent.kind, "external");
  assert.equal(shown.url, SHOP_URL, "No refused address may have moved the tab.");

  // The commit waits for the card on that tab: first the person says no, then yes.
  read = await agent.call("read", { tab });
  const send = { tab, ref: refOf(read, "button", "Send order") };
  const answerCard = async (action, earlier = null) => {
    const call = agent.call("click", send);
    // The card on the tab, as the panel shows it: its summary and the id that the person's answer must name.
    let card = null;
    await withTimeout((async () => {
      while (!(card = (await agentTabs(page)).find((entry) => entry.id === tab && entry.commit)?.commit)) await delay(200);
    })(), 30_000, "The commit card never appeared on the agent's tab.");
    assert.ok(typeof card.id === "string" && card.id, "The card must carry its id.");
    assert.match(card.summary, /Send order/, "The card must say what would be clicked.");
    await delay(750);
    // An answer that names no card, or another one, is refused and decides nothing.
    await assert.rejects(panel(page, { action, tab }), "An answer without the card's id must be refused.");
    await assert.rejects(panel(page, { action, tab, commit: `${card.id}x` }), "An answer for another card must be refused.");
    if (earlier) {
      // No reuse and no replay: a new card has a new id, and the answer to an earlier card decides nothing on it.
      assert.notEqual(card.id, earlier, "Every card must have its own id.");
      await assert.rejects(panel(page, { action: "commit-approve", tab, commit: earlier }), "The id of an earlier card must be refused on a new one.");
    }
    assert.ok((await agentTabs(page)).some((entry) => entry.id === tab && entry.commit?.id === card.id), "A refused answer must leave the card waiting.");
    assert.deepEqual(site.state.orders, [], "While the card waits, nothing is sent.");
    assert.equal(site.count("send:click"), 0);
    await panel(page, { action, tab, commit: card.id });
    return { answer: await call, id: card.id };
  };
  const declined = await answerCard("commit-refuse");
  assert.ok(refused(declined.answer) || proposed(declined.answer), `A declined commit must not run: ${declined.answer}`);
  await delay(750);
  assert.deepEqual(site.state.orders, [], "A declined commit sends nothing.");
  const approved = await answerCard("commit-approve", declined.id);
  // The submit replaces the page. The answer is either done, or "sent" with the page gone on: then nothing could read
  // the click, and what proves it is what follows here: one click on the send button and the order's arrival.
  assert.ok(ran(approved.answer) || wentOn(approved.answer), `The approved commit must run: ${approved.answer}`);
  await settled("send:click", 1);
  assert.equal(site.count("send:click"), 1, "The confirmed commit must click exactly once.");
  assert.equal(site.starting("stray:click:"), found.strayClicks, "The confirmed click adds no stray one.");
  assert.equal(site.count("document:click"), 3 + found.personClicks + found.pointClicks + found.raceClicks + found.strayClicks, "No click besides the ordinary one, the raced buttons' own, the called-off presses' stray ones and the confirmed one.");
  assert.deepEqual(site.state.orders, [{ name: "Mira", password: "" }], "The form must arrive with the typed name and no secret.");

  // Stop: the agent's calls are refused and its tabs are gone.
  read = await agent.call("read", { tab });
  const receipt = { tab, ref: refOf(read, "button", "Show receipt") };
  await expectRan("A click before Stop", "click", receipt);
  await settled("receipt:click", 1);
  await panel(page, { action: "stop-agent", tab });
  await expectRefused("A click after Stop", "stopped", "click", receipt);
  await expectRefused("A read after Stop", "stopped", "read", { tab });
  await expectRefused("A new tab after Stop", "stopped", "open", { url: SHOP_URL });
  await settled("receipt:click", 1);
  assert.equal(site.count("receipt:click"), 1, "After Stop nothing is clicked.");
  assert.equal(site.count("document:click"), 4 + found.personClicks + found.pointClicks + found.raceClicks + found.strayClicks, "No click after Stop.");
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
    ...(process.env.XAUTHORITY ? { XAUTHORITY: process.env.XAUTHORITY } : {}),
    ...(visible ? { HZ_JOURNEY_VISIBLE: "1" } : {}), HZ_JOURNEY_RACE_TRIES: String(RACE_TRIES),
    XDG_CONFIG_HOME: join(root, "config"), XDG_DATA_HOME: join(root, "data"), XDG_STATE_HOME: join(root, "state"),
    XDG_CACHE_HOME: join(root, "cache"), HZ_CONFIG_HOME: join(root, "config"), HZ_DATA_DIR: join(root, "data"),
    HZ_STATE_DIR: join(root, "state"), HZ_CACHE_DIR: join(root, "cache"), HZ_LOG_DIR: join(root, "logs"),
    HZ_NATIVE_TEST_ROOT: root, HZ_NATIVE_TEST_PUBLIC_MEDIA: "1", HZ_CAPABILITIES__AGENT_RUNNER: "journey",
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
  assert.equal(await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().some((window) => window.isVisible())), visible,
    visible ? "Under the virtual display the window must be shown." : "The journey's app must stay hidden.");
  try {
    if (journey === "heinzel") await heinzelJourney(page, origin);
    else await mcpJourney(page, origin);
  } catch (error) {
    // What the guest itself says when a step failed: its size, its visibility and what lies at the first button.
    console.error(`Guest facts: ${JSON.stringify(await guestFacts().catch((failed) => String(failed)))}`);
    console.error(`Found so far: ${JSON.stringify(found)}; page events: ${JSON.stringify(site.state.events)}`);
    // Diagnostic: what the agent's first calls answered, what the panel lists and every page the app holds.
    const scripted = await readFile(join(root, "sync", "trace.json"), "utf8").catch(() => "none");
    console.error(`First calls: ${JSON.stringify(trace)}; scripted model: ${scripted}`);
    console.error(`Panel: ${JSON.stringify(await panel(page, { action: "tabs" }).catch((failed) => String(failed)))}`);
    console.error(`Pages: ${JSON.stringify(await app.evaluate(({ webContents }) => webContents.getAllWebContents().map((contents) =>
      ({ url: contents.getURL().replace(/#.*$/, ""), loading: contents.isLoading(), attached: contents.debugger.isAttached() }))).catch((failed) => String(failed)))}`);
    console.error(`Guest dialled: ${JSON.stringify(site.state.connects)}`);
    throw error;
  }
  assertNothingElseDialled();
  assert.notEqual(await kept("proxy-calls"), "", "The product must have configured its guests' network itself; the fixture only answers in its place.");
  assert.equal(await kept("dialogs"), "", "No native question may have been asked.");
  assert.equal(await kept("public-media"), "", "Nothing may have been handed to the system's browser.");
  assert.equal(await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().some((window) => window.isVisible())), visible,
    visible ? "Under the virtual display the window must be shown." : "The journey's app must stay hidden.");
  if (visible) await appendFile(tallyFile, `${journey} ${found.raceInFrame}\n`);
  // Diagnostic: every check of the journey held; what follows is only the app's own quit.
  console.log(`Browser journey "${journey}": every check passed, closing the app. Found: ${JSON.stringify(found)}`);
  const exited = new Promise((done) => electronProcess.once("exit", (code, signal) => done({ code, signal })));
  await page.close();
  const result = await withTimeout(exited, 20_000, "Electron did not exit after close.").catch(async (failed) => {
    console.error(`The app did not quit. Main-process errors: ${await kept("main-errors") || "none recorded"}; phases: ${(await kept("phases")).split("\n").slice(-8).join(" | ")}`);
    throw failed;
  });
  stopped = true;
  assert.equal(result.code, 0, `Electron closed abnormally: ${JSON.stringify(result)}`);
  console.log(`Browser journey "${journey}" passed: read with refs, typed, clicked, the commit waited for the person, ` +
    "swapped targets, secret fields and non-public addresses were refused, and Stop ended the agent's actions. " +
    `Tab viewport before the person opened the panel: ${JSON.stringify(found.closed)}, after: ${JSON.stringify(found.shown)}. ` +
    `Race: the frame was shown in ${found.raceShown} of ${RACE_TRIES} tries, ${found.raceInFrame} presses landed in it, ${found.raceClicks} tries clicked the button first, ${found.strayClicks} called-off presses were heard by the page as a click beside the button, on: ${found.strayTargets.join(", ") || "nothing"}. Tries by the page's wait in ms: ${raceLine.join(" ")}. Seam (the engine's press held until the frame was drawn): ${found.seam.join(", ")}. The person's own click after a called-off press: ${found.personClicks ? `went through, tab paused by it: ${found.personPaused}` : "not part of this journey"}.`);
} finally {
  site?.close();
  await cleanupHarness({
    app, electronProcess, stopped, root, ownedPid, launchAttempted,
    loadPid: async () => Number(await readFile(join(root, "owned-pid"), "utf8")),
    processExists: (pid) => { try { process.kill(pid, 0); return true; } catch (error) { if (error.code === "ESRCH") return false; throw error; } },
    remove: (path) => rm(path, { recursive: true, force: true }),
  });
}
