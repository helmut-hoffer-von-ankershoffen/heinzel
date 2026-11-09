import assert from "node:assert/strict";
import test from "node:test";
import { ACTIONS, appMenuTemplate, helpLogLine, menuLogLine, HELP_URLS, isAllowedHelpUrl, menuScript } from "../src/app-menu.js";

test("the app menu offers About, Settings, Check for Updates and the standard entries", () => {
  const sent = [];
  const [app] = appMenuTemplate({ name: "Heinzelm\u00e4nnchen", send: (action) => sent.push(action), open: () => {} });
  assert.equal(app.label, "Heinzelm\u00e4nnchen");
  const labels = app.submenu.map((item) => item.label ?? item.role ?? item.type);
  assert.deepEqual(labels.filter((label) => label !== "separator"), [
    "About Heinzelm\u00e4nnchen", "Settings\u2026", "Check for Updates\u2026",
    "services", "Hide Heinzelm\u00e4nnchen", "hideOthers", "unhide", "Quit Heinzelm\u00e4nnchen",
  ]);
  const settings = app.submenu.find((item) => item.accelerator === "Command+,");
  for (const item of app.submenu.filter((entry) => entry.click)) item.click();
  assert.equal(settings.label, "Settings\u2026");
  assert.deepEqual(sent, ["about", "settings", "updates"]);
});

test("the page is told only a known menu action", () => {
  assert.match(menuScript("about"), /"hz:menu".*"about"/);
  assert.throws(() => menuScript("alert(1)"));
});

test("the Help menu opens only real pages on the allowed hosts, and web actions for the rest", () => {
  const sent = [], opened = [];
  const template = appMenuTemplate({ name: "Heinzelm\u00e4nnchen", send: (a) => sent.push(a), open: (u) => opened.push(u) });
  const help = template.find((menu) => menu.role === "help");
  const walk = (items) => items.flatMap((item) => (item.submenu ? walk(item.submenu) : item.click ? [item] : []));
  for (const item of walk(help.submenu)) item.click();
  assert.deepEqual(sent, ["help", "changelog", "doctor", "copy", "daemon"]);
  assert.deepEqual([...opened].sort(), Object.values(HELP_URLS).sort());
  for (const url of opened) assert.ok(isAllowedHelpUrl(url), url);
  for (const bad of ["http://heinzel.team/", "https://evil.example/", "https://github.com/other/x", "https://heinzel.team.evil.example/", "javascript:1"])
    assert.equal(isAllowedHelpUrl(bad), false, bad);
});

test("a menu action logs one structured line", () => {
  assert.deepEqual(JSON.parse(menuLogLine("about", "sent")), { event: "desktop.menu.action", action: "about", outcome: "sent" });
});

test("a Help link logs its host and whether it opened, never the path", () => {
  const opened = JSON.parse(helpLogLine("https://heinzel.team/privacy/", true));
  assert.deepEqual(opened, { event: "desktop.help.open", target_host: "heinzel.team", outcome: "opened" });
  const blocked = JSON.parse(helpLogLine("https://evil.example/x?token=1", false));
  assert.deepEqual(blocked, { event: "desktop.help.open", target_host: "evil.example", outcome: "blocked" });
  assert.equal(JSON.parse(helpLogLine("not a url", false)).target_host, "invalid");
});

const NAME = "Heinzelm\u00e4nnchen";
const labelsOf = (items) => items.filter((item) => item.type !== "separator").map((item) => item.label ?? item.role);

test("the menu bar runs App, File, Edit, View, Go, Window, Help", () => {
  const bar = appMenuTemplate({ name: NAME, send() {}, open() {} }).map((menu) => menu.label ?? menu.role);
  assert.deepEqual(bar, [NAME, "File", "editMenu", "View", "Go", "windowMenu", "help"]);
});

test("File and Go carry the accelerators of the web app's own screens", () => {
  const [, file, , , go] = appMenuTemplate({ name: NAME, send() {}, open() {} });
  assert.deepEqual(file.submenu.filter((i) => i.accelerator).map((i) => [i.label, i.accelerator]), [
    ["New Chat", "CommandOrControl+N"],
  ]);
  assert.equal(file.submenu.at(-1).role, "close");
  assert.deepEqual(go.submenu.map((i) => [i.label, i.accelerator]), [
    ["Today", "CommandOrControl+1"], [NAME, "CommandOrControl+2"], ["Team", "CommandOrControl+3"], ["Plugins", "CommandOrControl+4"],
  ]);
});

test("View lists the panels with their accelerators, then zoom, reload and full screen; dev tools only in development", () => {
  const view = (devTools) => appMenuTemplate({ name: NAME, send() {}, open() {}, devTools })[3].submenu;
  assert.deepEqual(view(false).filter((i) => i.accelerator).map((i) => [i.label, i.accelerator]), [
    ["Search\u2026", "CommandOrControl+K"], ["Toggle Sidebar", "CommandOrControl+B"], ["Show Terminal", "CommandOrControl+J"],
    ["Show Browser", "CommandOrControl+Shift+B"], ["Show Side Chat", "CommandOrControl+;"],
    ["Show Notifications", "CommandOrControl+Shift+N"],
  ]);
  const roles = (items) => items.filter((i) => i.role).map((i) => i.role);
  assert.deepEqual(roles(view(false)), ["resetZoom", "zoomIn", "zoomOut", "reload", "togglefullscreen"]);
  assert.ok(roles(view(true)).includes("toggleDevTools"));
});

test("every menu item sends a listed action or opens an allowed link, in menu order", () => {
  const sent = [], opened = [];
  const template = appMenuTemplate({ name: NAME, send: (a) => sent.push(a), open: (u) => opened.push(u) });
  const walk = (items) => items.flatMap((item) => (item.submenu ? walk(item.submenu) : item.click ? [item] : []));
  for (const item of walk(template.flatMap((menu) => menu.submenu ?? []))) item.click();
  assert.deepEqual([...new Set(sent)].sort(), [...ACTIONS].sort());
  for (const url of opened) assert.ok(isAllowedHelpUrl(url), url);
  assert.match(menuScript("go:today"), /"hz:menu".*"go:today"/);
  assert.throws(() => menuScript("x"));
  assert.ok(labelsOf(template.at(-1).submenu).includes("Report an Issue\u2026"));
});

test("About opens the web dialog, never the native panel", async () => {
  const sent = [];
  const [app] = appMenuTemplate({ name: NAME, send: (a) => sent.push(a), open() {} });
  const about = app.submenu[0];
  assert.equal(about.role, undefined);
  about.click();
  assert.deepEqual(sent, ["about"]);
  const { readFile } = await import("node:fs/promises");
  const main = await readFile(new URL("../src/main.js", import.meta.url), "utf8");
  assert.match(main, /setAboutPanelOptions\(\{ applicationName: app\.name, applicationVersion: app\.getVersion\(\), version: "" \}\)/);
});
