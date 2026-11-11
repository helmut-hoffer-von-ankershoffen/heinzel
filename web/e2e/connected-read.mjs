import assert from "node:assert/strict";
import { openChatJourney, reportJourneyFailure, revokeAfterReload, startChatJourney } from "./chat-ready.mjs";

const { axe, browser, context, page, token, url } = await openChatJourney();
const isCreation = (request) =>
  request.method() === "POST" && new URL(request.url()).pathname === "/api/heinzel/new-chat";
const creation = { sent: false, failed: false, status: null };
page.on("request", (request) => {
  if (isCreation(request)) creation.sent = true;
});
page.on("requestfailed", (request) => {
  if (isCreation(request)) creation.failed = true;
});
page.on("response", (response) => {
  if (isCreation(response.request())) creation.status = response.status();
});

try {
  await startChatJourney(page, url);
  const panel = page.getByRole("region", { name: "Connected read review" });
  await panel.getByLabel("Read operation").selectOption("calendar.events");
  await panel
    .getByLabel("Fixed arguments (JSON)")
    .fill(JSON.stringify({ window: { since: "2026-01-01T00:00:00Z", until: "2026-01-02T00:00:00Z" } }));
  await panel.getByRole("button", { name: "Prepare review" }).click();
  await panel.getByText("Account: fixture-account").waitFor();
  await panel.getByRole("button", { name: "Review access" }).click();
  await panel.getByRole("heading", { name: "Review connected read" }).waitFor();
  await page.evaluate(axe);
  const violations = await panel.evaluate(async (node) => (await window.axe.run(node)).violations.map(({ id }) => id));
  assert.deepEqual(violations, []);
  await panel.getByRole("button", { name: "Confirm" }).click();
  await panel.getByText(/Recorded state: approved/).waitFor();
  await page.getByRole("button", { name: "Queue message" }).click();
  await page.getByText("Read complete.").waitFor({ timeout: 30000 });
  await page.getByText(/Tool activity for this reply/).click();
  await page.getByText("connected_read · completed").waitFor();
  await revokeAfterReload(page, panel);
  console.log(JSON.stringify({ accepted: true }));
} catch (error) {
  const chatSelected = await page
    .getByRole("tab", { name: "Chat", exact: true, selected: true })
    .isVisible()
    .catch(() => null);
  const chatInUrl = new URL(page.url()).searchParams.has("chat");
  console.error("Connected read startup:", JSON.stringify({ creation, chatSelected, chatInUrl }));
  await reportJourneyFailure(page, token, error);
} finally {
  await context.close();
  await browser.close();
}
