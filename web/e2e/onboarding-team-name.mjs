// Fresh first-run setup: type a team name in the Team step, reload, and the name is still there (and on the profile).
import assert from "node:assert/strict";
import { chromium, webkit } from "playwright-core";

const url = new URL(process.argv[2]);
url.pathname = "/doctor";
const [, , , engine, viewport] = process.argv;
const size = viewport === "phone" ? { width: 390, height: 844 } : { width: 1280, height: 900 };
const browser = await (engine === "webkit" ? webkit : chromium).launch({ headless: true });
const context = await browser.newContext({ serviceWorkers: "block", viewport: size });
const page = await context.newPage();

async function toTeamStep() {
  await page.locator("#splash").waitFor({ state: "hidden", timeout: 30000 });
  const wizard = page.getByRole("dialog", { name: "Set up hz" });
  await wizard.waitFor({ timeout: 15000 });
  for (let i = 0; i < 4 && !(await wizard.getByRole("heading", { name: "Your team" }).isVisible()); i++)
    await wizard.getByRole("button", { name: "Next" }).click();
  await wizard.getByRole("heading", { name: "Your team" }).waitFor();
  return wizard;
}

try {
  await page.goto(url.href);
  let wizard = await toTeamStep();
  const field = wizard.getByLabel("Team name");
  assert.equal(await field.inputValue(), "");
  await field.fill("Payments");
  await field.press("Enter");
  await wizard.getByText("Saved").first().waitFor();
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);

  await page.reload();
  wizard = await toTeamStep();
  assert.equal(await wizard.getByLabel("Team name").inputValue(), "Payments");

  await page.goto(new URL("/profile", url).href);
  await page.getByText("Payments", { exact: true }).waitFor();
  console.log(JSON.stringify({ team: "Payments" }));
} finally {
  await browser.close();
}
