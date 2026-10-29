import { chromium } from "playwright-core";

const supplied = process.argv[2];
if (!supplied) throw new Error("Missing isolated web URL");
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ serviceWorkers: "block" });
const page = await context.newPage();
const token = new URL(supplied).hash.match(/(?:^#|&)token=([^&]+)/)?.[1];

try {
  const target = new URL(supplied);
  target.pathname = "/heinzel";
  await page.goto(target.href);
  await page.locator("#splash").waitFor({ state: "hidden", timeout: 30000 });
  const setup = page.getByRole("dialog");
  await setup.waitFor({ state: "visible", timeout: 15000 }).catch(() => {});
  if (await setup.isVisible().catch(() => false)) await setup.getByRole("button", { name: "Close setup" }).click();
  await page.getByRole("button", { name: "Create a personal Heinzel" }).click();
  await page.getByRole("button", { name: "Continue to schedule setup" }).click();
  await page.getByLabel("Message your Heinzel").fill("notify me every 5 minutes");
  await page.getByRole("button", { name: "Prepare schedule review" }).click();
  await page.getByText(/configured agent runner could not answer/i).waitFor();

  const readSchedule = async () =>
    await page.evaluate(async () => {
      const session = new URLSearchParams(location.search).get("chat");
      const credential = sessionStorage.getItem("hz.token");
      const response = await fetch(`/api/heinzel/session?session_id=${encodeURIComponent(session ?? "")}`, {
        headers: { Authorization: `Bearer ${credential}` },
      });
      if (!response.ok) throw new Error(`Isolated schedule read failed: ${response.status}`);
      return response.json();
    });
  const beforeProposal = await readSchedule();
  if (beforeProposal.schedule !== null) throw new Error("An unverified runner changed the schedule");
  if (await page.getByRole("button", { name: "Confirm", exact: true }).count())
    throw new Error("An unverified runner produced an approval action");

  await page.getByRole("button", { name: "Propose check-in schedule" }).click();
  await page.getByText("proposed", { exact: true }).waitFor();
  const inactive = await readSchedule();
  if (inactive.schedule?.status !== "proposed")
    throw new Error("Explicit proposal did not leave the schedule inactive");
  await page.getByRole("button", { name: "Review activation in chat" }).click();
  await page.getByRole("button", { name: "Confirm" }).first().waitFor();
  const beforeActivationApproval = await readSchedule();
  if (beforeActivationApproval.schedule?.status !== "proposed") throw new Error("Schedule activated before review");
  await page.getByRole("button", { name: "Confirm" }).first().click();
  await page.getByText("active", { exact: true }).waitFor();
  const active = await readSchedule();
  if (active.schedule?.status !== "active") throw new Error("Human confirmation did not activate schedule");
  console.log(JSON.stringify({ browser: "approved", session: active.session_id }));
} catch (error) {
  const message = error instanceof Error ? (error.stack ?? error.message) : String(error);
  const visible = await page
    .locator("body")
    .innerText()
    .catch(() => "");
  process.stderr.write(
    `${message}\n${visible}`.replaceAll(decodeURIComponent(token ?? ""), "<redacted-session>") + "\n",
  );
  process.exitCode = 1;
} finally {
  await context.close();
  await browser.close();
}
