// The running app against design/tokens.json, in light and dark, at phone and desktop sizes (Chromium).
// Usage: node e2e/design-tokens.mjs URL. Run by `make design-check` (scripts/design_check.py), which starts `hz ui`.
// Values come from the token file, never from here. Computed styles are read in the page, so what is checked is what
// a person sees: the tokens on <html>, the body, a primary button's fill, text and corners, and a card's corners.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { chromium } from "playwright-core";

const [url] = process.argv.slice(2);
assert.ok(url, "Usage: node e2e/design-tokens.mjs URL");
const data = JSON.parse(readFileSync(new URL("../../design/tokens.json", import.meta.url), "utf8"));
const colour = (name) => data.color.tokens.find((token) => token.name === name).value;
const radius = (name) => data.radius.tokens.find((token) => token.name === name).value;
const modes = ["light", "dark"];
const viewports = [
  { name: "phone", width: 375, height: 812 },
  { name: "desktop", width: 1280, height: 900 },
];
const origin = new URL(url);
const hash = origin.hash;
const api = (path) => `${origin.origin}/api${path}`;
const headers = { Authorization: `Bearer ${hash.slice("#token=".length)}` };

const browser = await chromium.launch().catch(() => chromium.launch({ channel: "chrome" }));
const observed = [];

/** Resolve a colour or length the way the page does, so "#b4532f", "rgb(...)" and "0.5rem" compare as computed. */
const resolve = (page, kind, value) =>
  page.evaluate(
    ([kind, value]) => {
      const probe = document.createElement("div");
      probe.style.position = "absolute";
      document.body.append(probe);
      let result;
      if (kind === "colour") {
        probe.style.color = value;
        result = getComputedStyle(probe).color;
      } else {
        probe.style.width = value;
        result = getComputedStyle(probe).width;
      }
      probe.remove();
      return result;
    },
    [kind, value],
  );

async function measure(page) {
  return await page.evaluate(() => {
    const root = getComputedStyle(document.documentElement);
    const body = getComputedStyle(document.body);
    let button = [...document.querySelectorAll("button")].find((candidate) => candidate.classList.contains("bg-primary"));
    let card = document.querySelector(".rounded-xl");
    const injected = [];
    if (!button) {
      button = document.createElement("button");
      button.className = "inline-flex items-center justify-center rounded-lg bg-primary text-primary-foreground h-9 px-3";
      button.textContent = "Probe";
      injected.push(button);
    }
    if (!card) {
      card = document.createElement("div");
      card.className = "rounded-xl border border-border bg-card p-3";
      injected.push(card);
    }
    for (const element of injected) document.body.append(element);
    const buttonStyle = getComputedStyle(button);
    const cardStyle = getComputedStyle(card);
    const result = {
      dark: document.documentElement.classList.contains("dark"),
      primary: root.getPropertyValue("--color-primary").trim(),
      background: root.getPropertyValue("--color-background").trim(),
      bodyBackground: body.backgroundColor,
      bodyColor: body.color,
      buttonBackground: buttonStyle.backgroundColor,
      buttonColor: buttonStyle.color,
      buttonRadius: buttonStyle.borderTopLeftRadius,
      cardRadius: cardStyle.borderTopLeftRadius,
      injected: injected.length,
    };
    for (const element of injected) element.remove();
    return result;
  });
}

async function check(page, mode, label) {
  const slot = mode === "dark" ? "dark" : "light";
  const want = async (name) => resolve(page, "colour", colour(name)[slot]);
  const got = await measure(page);
  assert.equal(got.dark, mode === "dark", `${label}: .dark class`);
  assert.equal(await resolve(page, "colour", got.primary), await want("color-primary"), `${label}: --color-primary`);
  assert.equal(await resolve(page, "colour", got.background), await want("color-background"), `${label}: --color-background`);
  assert.equal(got.bodyBackground, await want("color-background"), `${label}: body background`);
  assert.equal(got.bodyColor, await want("color-foreground"), `${label}: body colour`);
  assert.equal(got.buttonBackground, await want("color-primary"), `${label}: primary button fill`);
  assert.equal(got.buttonColor, await want("color-primary-foreground"), `${label}: primary button text`);
  assert.equal(got.buttonRadius, await resolve(page, "length", radius("radius-lg")), `${label}: button radius`);
  assert.equal(got.cardRadius, await resolve(page, "length", radius("radius-xl")), `${label}: card radius`);
  observed.push({ label, ...got, expectedPrimary: colour("color-primary")[slot] });
  return got;
}

async function open(viewport, mode) {
  const context = await browser.newContext({ viewport, colorScheme: mode });
  const page = await context.newPage();
  await page.goto(url);
  await page.waitForSelector("#root button, #root a", { timeout: 30000 });
  await page.waitForFunction((dark) => document.documentElement.classList.contains("dark") === dark, mode === "dark");
  return { context, page };
}

try {
  for (const viewport of viewports) {
    for (const mode of modes) {
      const { context, page } = await open(viewport, mode);
      await check(page, mode, `default ${mode} ${viewport.name}`);
      await context.close();
    }
  }
  // A theme plugin that restates the default palette must look the same: compare the Heinzelmännchen theme if present.
  const list = await (await fetch(api("/system/theme/list"), { headers })).json();
  const other = list.themes.find((theme) => /heinzel/i.test(theme.name) && theme.name !== "default");
  if (other) {
    const before = list.selected;
    try {
      await fetch(`${api("/system/theme/use")}?name=${encodeURIComponent(other.name)}`, { method: "POST", headers });
      for (const viewport of viewports) {
        for (const mode of modes) {
          const { context, page } = await open(viewport, mode);
          await check(page, mode, `${other.name} ${mode} ${viewport.name}`);
          await context.close();
        }
      }
    } finally {
      await fetch(`${api("/system/theme/use")}?name=${encodeURIComponent(before)}`, { method: "POST", headers });
    }
  } else {
    console.error("DESIGN_NOTE: no Heinzelmännchen theme installed; only the default theme was compared");
  }
} finally {
  await browser.close();
}
console.log(JSON.stringify({ evidence: "computed styles in Chromium", observed }));
