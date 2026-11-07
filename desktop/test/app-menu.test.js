import assert from "node:assert/strict";
import test from "node:test";
import { appMenuTemplate, helpLogLine, menuLogLine, HELP_URLS, isAllowedHelpUrl, menuScript } from "../src/app-menu.js";

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
  assert.deepEqual(sent, ["changelog", "doctor", "copy", "daemon"]);
  assert.deepEqual(opened, Object.values(HELP_URLS));
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
