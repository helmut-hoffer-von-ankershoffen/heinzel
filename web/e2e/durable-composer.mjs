import assert from "node:assert/strict";
import { chromium } from "playwright-core";

const [sessionUrl] = process.argv.slice(2);
if (!sessionUrl) throw new Error("Usage: node e2e/durable-composer.mjs SESSION_URL");

const firstSession = "hz-01ARZ3NDEKTSV4RRFFQ69G5FAV";
const secondSession = "hz-01ARZ3NDEKTSV4RRFFQ69G5FAW";
const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await context.newPage();
const updateVersion = "0.7.31";
let restarted = false;
let updated = false;
const submissions = [];
const answerRequested = Promise.withResolvers();
const finishAnswer = Promise.withResolvers();

function sessionUrlFor(id) {
  const address = new URL(sessionUrl);
  address.pathname = "/heinzel";
  address.search = new URLSearchParams({ chat: id }).toString();
  return address.toString();
}

await page.route("**/api/update-status", (route) =>
  route.fulfill({
    json: {
      current: updated ? updateVersion : "0.7.30",
      latest: updateVersion,
      installed_at: "2026-10-28T12:00:00Z",
      available: !updated,
      supported: true,
      reason: null,
    },
  }),
);
await page.route("**/api/heinzel/session**", (route) => {
  const address = new URL(route.request().url());
  if (address.pathname !== "/api/heinzel/session") return route.continue();
  const id = address.searchParams.get("session_id");
  assert.ok(id === firstSession || id === secondSession, `unexpected session ${id}`);
  return route.fulfill({
    json: {
      session_id: id,
      name: id === firstSession ? "First helper" : "Second helper",
      schedule: null,
      messages: [],
    },
  });
});
await page.route("**/api/heinzel/chat", async (route) => {
  const body = route.request().postDataJSON();
  submissions.push(body);
  if (submissions.length === 1) return route.fulfill({ status: 503, json: { detail: "acknowledgement lost" } });
  return route.fulfill({
    json: { session_id: body.session_id, request_id: body.request_id, disposition: "queued", inbox_position: 1 },
  });
});
await page.route("**/api/heinzel/answer-chat", async (route) => {
  const body = route.request().postDataJSON();
  answerRequested.resolve(body);
  await finishAnswer.promise;
  return route.fulfill({
    json: {
      session_id: body.session_id,
      request_id: body.request_id,
      message: { id: "fixture-answer", seq: 3, role: "assistant", text: "The draft is ready.", status: "completed" },
    },
  });
});
await page.route("**/api/system/update", async (route) => {
  updated = true;
  return route.fulfill({
    json: {
      before: "0.7.30",
      after: updateVersion,
      latest: updateVersion,
      updated: true,
      message: "Isolated browser fixture update.",
      next: [],
      pinned_to: null,
      plugins: [],
    },
  });
});
await page.route("**/api/lifecycle", (route) =>
  route.fulfill({
    json: {
      instance: restarted ? "fixture-new-instance" : "fixture-old-instance",
      version: restarted ? updateVersion : "0.7.30",
      restartable: true,
    },
  }),
);
await page.route("**/api/restart", (route) => {
  restarted = true;
  return route.fulfill({ json: { restarting: true, instance: "fixture-old-instance" } });
});
await page.route("**/api/lifecycle/verify", (route) =>
  route.fulfill({ json: { instance: "fixture-new-instance", version: updateVersion, matches: true, mismatched: [] } }),
);
await page.route("**/api/frontend", async (route) => {
  const response = await route.fetch();
  const frontend = await response.json();
  return route.fulfill({ response, json: { ...frontend, version: updateVersion } });
});

try {
  await page.goto(sessionUrlFor(firstSession));
  await page.locator("#splash").waitFor({ state: "hidden", timeout: 15_000 });
  const composer = page.getByLabel("Message your Heinzel");
  await composer.waitFor();
  await composer.fill("First session draft survives the update");
  const firstKey = `hz.durable.composer.${firstSession}`;
  const secondKey = `hz.durable.composer.${secondSession}`;
  assert.match(await page.evaluate((key) => sessionStorage.getItem(key) ?? "", firstKey), /First session draft/);

  await page.goto(sessionUrlFor(secondSession));
  const secondComposer = page.getByLabel("Message your Heinzel");
  await page.getByRole("heading", { name: "Second helper" }).waitFor();
  await secondComposer.waitFor();
  assert.equal(await secondComposer.inputValue(), "", "another durable session must not inherit the first draft");
  assert.equal(await page.evaluate((key) => sessionStorage.getItem(key), secondKey), null);

  await page.goto(sessionUrlFor(firstSession));
  await page.getByLabel("Message your Heinzel").waitFor();
  assert.equal(await page.getByLabel("Message your Heinzel").inputValue(), "First session draft survives the update");
  assert.equal(await page.evaluate((key) => sessionStorage.getItem(key), secondKey), null);
  await page.getByRole("button", { name: "Update available" }).click();
  await page.getByRole("button", { name: "Update hz and plugins" }).waitFor();
  const reloaded = page.waitForEvent("load", { timeout: 30_000 });
  await page.getByRole("button", { name: "Update hz and plugins" }).click();
  await reloaded;
  await page.locator("#splash").waitFor({ state: "hidden", timeout: 15_000 });
  await page.waitForFunction(() => sessionStorage.getItem("hz.update.receipt") === null, null, { timeout: 15_000 });
  assert.equal(await page.getByLabel("Message your Heinzel").inputValue(), "First session draft survives the update");

  await page.goto(sessionUrlFor(secondSession));
  await page.getByLabel("Message your Heinzel").waitFor();
  assert.equal(await page.getByLabel("Message your Heinzel").inputValue(), "");
  await page.goto(sessionUrlFor(firstSession));
  await page.getByLabel("Message your Heinzel").waitFor();
  await page.getByRole("button", { name: "Queue message" }).click();
  await page.getByRole("button", { name: "Retry the same message" }).waitFor();
  const savedPending = JSON.parse(await page.evaluate((key) => sessionStorage.getItem(key), firstKey));
  assert.equal(savedPending.pending.message, "First session draft survives the update");
  const requestId = savedPending.pending.request_id;
  await page.reload();
  await page.locator("#splash").waitFor({ state: "hidden", timeout: 15_000 });
  await page.getByRole("button", { name: "Retry the same message" }).waitFor();
  const savedAfterReload = JSON.parse(await page.evaluate((key) => sessionStorage.getItem(key), firstKey));
  assert.equal(savedAfterReload.pending.request_id, requestId);
  assert.equal(await page.getByLabel("Message your Heinzel").inputValue(), "First session draft survives the update");
  const retryResponse = page.waitForResponse(
    (response) => new URL(response.url()).pathname === "/api/heinzel/chat" && response.status() === 200,
  );
  await page.getByRole("button", { name: "Retry the same message" }).click();
  await retryResponse;
  assert.equal(submissions.length, 2);
  assert.deepEqual(submissions[1], submissions[0], "retry after reload must use the exact same session and request id");
  assert.equal(submissions[0].session_id, firstSession);
  assert.equal(submissions[0].request_id, requestId);
  assert.deepEqual(await answerRequested.promise, { session_id: firstSession, request_id: requestId });
  const acceptedPending = JSON.parse(await page.evaluate((key) => sessionStorage.getItem(key), firstKey));
  assert.equal(acceptedPending.pending.accepted, true, "acceptance alone must preserve a still-unanswered message");
  assert.equal(acceptedPending.pending.request_id, requestId);
  finishAnswer.resolve();
  await page.waitForFunction((key) => sessionStorage.getItem(key) === null, firstKey);
  assert.equal(await page.evaluate((key) => sessionStorage.getItem(key), firstKey), null);

  await page.goto(sessionUrlFor(secondSession));
  await page.getByLabel("Message your Heinzel").waitFor();
  assert.equal(await page.getByLabel("Message your Heinzel").inputValue(), "");
  console.log(
    JSON.stringify({
      updateReload: "verified",
      firstSession,
      secondSession,
      requestId,
      submissions: submissions.length,
    }),
  );
} finally {
  finishAnswer.resolve();
  await context.close();
  await browser.close();
}
