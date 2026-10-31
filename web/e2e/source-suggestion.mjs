import assert from "node:assert/strict";
import { openChatJourney, revokeAfterReload, startChatJourney } from "./chat-ready.mjs";

const { axe, browser, context, page, token, url } = await openChatJourney();
let reviewRequests = 0;
page.on("request", (request) => {
  if (new URL(request.url()).pathname === "/api/heinzel/prepare-suggested-source") reviewRequests++;
});
try {
  await startChatJourney(page, url);
  const panel = page.getByRole("region", { name: "Connected read review" });
  await page.getByRole("button", { name: "Queue message" }).click();
  await page.getByText("Suggestion recorded.").waitFor({ timeout: 30000 });
  await panel.getByText(/has not contacted a provider/).waitFor();
  await panel.getByText("documents.read", { exact: true }).waitFor();
  assert.equal(reviewRequests, 0, "Suggestion must not contact the provider");
  await panel.getByRole("button", { name: "Review source" }).click();
  await panel.getByText("Account: fixture-account").waitFor();
  await panel.getByRole("button", { name: "Review access" }).click();
  await panel.getByRole("heading", { name: "Review connected read" }).waitFor();
  await page.evaluate(axe);
  const violations = await panel.evaluate(async (node) => (await window.axe.run(node)).violations.map(({ id }) => id));
  assert.deepEqual(violations, []);
  await panel.getByRole("button", { name: "Confirm" }).click();
  await panel.getByText(/Recorded state: approved/).waitFor();
  await page.getByLabel("Message your Heinzel").fill("Read the approved source now.");
  await page.getByRole("button", { name: "Queue message" }).click();
  await page.getByText("Source read finished.").waitFor({ timeout: 30000 });
  await page.getByText("Approved read: first recorded result.").waitFor();
  await revokeAfterReload(page, panel);
  console.log(JSON.stringify({ accepted: true }));
} catch (error) {
  const message = error instanceof Error ? (error.stack ?? error.message) : String(error);
  const visible = await page
    .locator("body")
    .innerText()
    .catch(() => "");
  process.stderr.write(`${message}\n${visible}`.replaceAll(decodeURIComponent(token), "<redacted-session>") + "\n");
  process.exitCode = 1;
} finally {
  await context.close();
  await browser.close();
}
