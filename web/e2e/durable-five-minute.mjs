import { chromium, webkit } from "playwright-core";
import { fetchOnce } from "./fetch-once.mjs";
import { closeRouted } from "./route-settlement.mjs";

const supplied = process.argv[2];
if (!supplied) throw new Error("Missing isolated web URL");
const browser = await (process.argv[3] === "webkit" ? webkit : chromium).launch({ headless: true });
const context = await browser.newContext({
  serviceWorkers: "block",
  viewport: { width: 390, height: 844 },
  isMobile: true,
  hasTouch: true,
});
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
  // Style first: the draft has a preview and a name field, and nothing is created until it is saved.
  await page.getByRole("button", { name: "Create a personal Heinzel" }).click();
  await page.getByRole("heading", { name: "Your new Heinzel" }).waitFor();
  const draftSave = page.getByRole("button", { name: "Save and chat" });
  if (!(await draftSave.isDisabled())) throw new Error("A nameless draft could be saved");
  const listed = await page.evaluate(async () => {
    const response = await fetch("/api/heinzel/sessions", {
      headers: { Authorization: `Bearer ${sessionStorage.getItem("hz.token")}` },
    });
    return (await response.json()).sessions.length;
  });
  if (listed !== 0) throw new Error("Opening the draft created a Heinzel");
  await page.getByLabel("Name", { exact: true }).fill("Draft helper");
  await page.getByRole("radiogroup", { name: "Hat colour" }).getByRole("radio", { name: "Teal" }).click();
  const creation = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/heinzel/new-chat");
  await draftSave.click();
  const selected = (await (await creation).json()).session_id;
  if (!selected) throw new Error("The created profile was not selected");
  // Saving lands in the new Heinzel's conversation, which shows the chosen look.
  await page.waitForURL((url) => url.searchParams.get("chat") === selected);
  await page.getByLabel("Message your Heinzel").waitFor();
  const header = page.getByRole("img", { name: /^Draft helper/ });
  await header.waitFor();
  if ((await header.locator('[fill="#1F9488"]').count()) === 0)
    throw new Error("The chat does not show the chosen hat");
  // Back on the page, the saved Heinzel is the selected card and can still be renamed.
  await page.getByRole("tab", { name: "Page", exact: true }).click();
  const own = page.getByRole("region", { name: "Personal Heinzels" });
  const picker = own.getByRole("radiogroup", { name: "Personal Heinzel" });
  await picker.getByRole("radio", { name: /^Draft helper/, checked: true }).waitFor();
  await own.getByLabel("Name", { exact: true }).waitFor();
  let saves = 0;
  await page.route("**/api/heinzel/profile", async (route) => {
    saves += 1;
    const actual = await fetchOnce(route);
    if (!actual.ok()) throw new Error(`The isolated profile save failed: ${actual.status()}`);
    await route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ detail: "lost acknowledgement" }),
    });
  });
  await own.getByLabel("Name", { exact: true }).fill("Mobile helper");
  await own.getByRole("button", { name: "Save name and look" }).click();
  await own.getByRole("button", { name: "Retry save" }).waitFor();
  await page.reload();
  await page.locator("#splash").waitFor({ state: "hidden", timeout: 30000 });
  await page.getByRole("tab", { name: "Page", exact: true }).click();
  await picker.getByRole("radio", { name: /^Mobile helper/ }).click();
  await own.getByRole("button", { name: "Check saved profile" }).click();
  await own.getByLabel("Name", { exact: true }).waitFor();
  if ((await own.getByLabel("Name", { exact: true }).inputValue()) !== "Mobile helper" || saves !== 1)
    throw new Error("Reload did not recover the committed profile without another save");
  await page.unroute("**/api/heinzel/profile");
  // The conversation is already linked; the button still brings its tab forward on a phone.
  await own.getByRole("button", { name: "Chat with Mobile helper" }).click();
  await page.waitForURL((url) => url.searchParams.get("chat") === selected);
  await page.getByLabel("Message your Heinzel").waitFor();
  const readSchedule = async () =>
    await page.evaluate(async (expected) => {
      const session = new URLSearchParams(location.search).get("chat");
      if (session !== expected) throw new Error("The selected personal conversation changed");
      const credential = sessionStorage.getItem("hz.token");
      const response = await fetch(`/api/heinzel/session?session_id=${encodeURIComponent(session ?? "")}`, {
        headers: { Authorization: `Bearer ${credential}` },
      });
      if (!response.ok) throw new Error(`Isolated schedule read failed: ${response.status}`);
      const snapshot = await response.json();
      if (snapshot.session_id !== expected) throw new Error("The schedule belongs to another personal conversation");
      return snapshot;
    }, selected);
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
  await page.getByRole("button", { name: "Review pause in chat" }).click();
  const pause = page.getByRole("heading", { name: "Review pause of this schedule" }).locator("..");
  await pause.getByRole("button", { name: "Confirm" }).click();
  await page.getByText("paused", { exact: true }).waitFor();
  const paused = await readSchedule();
  if (paused.schedule?.status !== "paused" || paused.schedule.revision !== active.schedule.revision + 1)
    throw new Error("Pause did not update the reviewed schedule exactly once");
  await page.getByRole("button", { name: "Review resume in chat" }).click();
  const resume = page.getByRole("heading", { name: "Review resuming this schedule" }).locator("..");
  await resume.getByRole("button", { name: "Confirm" }).click();
  await page.getByText("active", { exact: true }).waitFor();
  const resumed = await readSchedule();
  if (resumed.schedule?.status !== "active" || resumed.schedule.revision !== paused.schedule.revision + 1)
    throw new Error("Resume did not update the reviewed schedule exactly once");
  if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth))
    throw new Error("The mobile page overflows horizontally");
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
  await closeRouted(context);
  await browser.close();
}
