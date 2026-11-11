import assert from "node:assert/strict";
import { chromium } from "playwright-core";
import { fetchOnce } from "./fetch-once.mjs";

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
const updateRequests = { install: 0, restart: 0, lifecycle: 0, frontend: 0, verified: 0 };
const submissions = [];
const answerRequested = Promise.withResolvers();
const finishAnswer = Promise.withResolvers();
const autoFactsRequested = Promise.withResolvers();
const publishAutoFacts = Promise.withResolvers();

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
// The app streams the update (`POST /api/stream/system/update`) to show its steps; either route counts as an install.
const isUpdate = (url) => ["/api/system/update", "/api/stream/system/update"].includes(url.pathname);
await page.route(isUpdate, async (route) => {
  updateRequests.install++;
  updated = true;
  const result = {
    before: "0.7.30",
    after: updateVersion,
    latest: updateVersion,
    updated: true,
    message: "Isolated browser fixture update.",
    next: [],
    pinned_to: null,
    plugins: [],
  };
  const event = (kind, data) => `event: ${kind}\ndata: ${JSON.stringify(data)}\n\n`;
  return route.fulfill({
    contentType: "text/event-stream",
    body:
      event("phase", { id: "install", label: "Installing the update", state: "done" }) +
      event("result", { data: result }),
  });
});
await page.route("**/api/lifecycle", (route) => {
  updateRequests.lifecycle++;
  return route.fulfill({
    json: {
      instance: restarted ? "fixture-new-instance" : "fixture-old-instance",
      version: restarted ? updateVersion : "0.7.30",
      restartable: true,
    },
  });
});
await page.route("**/api/restart", (route) => {
  updateRequests.restart++;
  restarted = true;
  return route.fulfill({ json: { restarting: true, instance: "fixture-old-instance" } });
});
await page.route("**/api/lifecycle/verify", (route) => {
  updateRequests.verified++;
  return route.fulfill({
    json: { instance: "fixture-new-instance", version: updateVersion, matches: true, mismatched: [] },
  });
});
await page.route("**/api/frontend", async (route) => {
  updateRequests.frontend++;
  const response = await fetchOnce(route);
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

  const autoFacts = await page.evaluate(async () => {
    const response = await fetch("/api/auto-update", {
      headers: { Authorization: `Bearer ${sessionStorage.getItem("hz.token") ?? ""}` },
    });
    if (!response.ok) throw new Error("The isolated automatic-update facts were unavailable");
    return response.json();
  });
  const autoExplanation =
    "Automatic updates stay off in this isolated browser fixture while the manual update is verified.";
  await page.route("**/api/auto-update", async (route) => {
    autoFactsRequested.resolve();
    await publishAutoFacts.promise;
    return route.fulfill({ json: { ...autoFacts, held: autoExplanation, development: autoExplanation } });
  });
  await page.goto(sessionUrlFor(firstSession));
  await page.getByLabel("Message your Heinzel").waitFor();
  assert.equal(await page.getByLabel("Message your Heinzel").inputValue(), "First session draft survives the update");
  assert.equal(await page.evaluate((key) => sessionStorage.getItem(key), secondKey), null);
  await page.getByRole("button", { name: "Updates available" }).click();
  const updateButton = page.getByRole("button", { name: "Update now" });
  await updateButton.waitFor();
  await autoFactsRequested.promise;
  const reloaded = page.waitForEvent("load", { timeout: 30_000 });
  try {
    const press = async () => {
      await updateButton.hover();
      const before = await updateButton.boundingBox();
      assert.ok(before);
      await page.mouse.move(before.x + before.width / 2, before.y + before.height / 2);
      await updateButton.evaluate((button, explanation) => {
        const geometry = () => {
          const { x, y, width, height } = button.getBoundingClientRect();
          return { x, y, width, height };
        };
        window.durablePressGeometry = null;
        const observer = new MutationObserver(() => {
          if (!document.body.textContent.includes(explanation)) return;
          observer.disconnect();
          const delivery = geometry();
          requestAnimationFrame(() => {
            const frame = geometry();
            requestAnimationFrame(() => {
              window.durablePressGeometry = { delivery, frame, settled: geometry() };
            });
          });
        });
        observer.observe(document.body, { subtree: true, childList: true, characterData: true });
      }, autoExplanation);
      await page.mouse.down();
      publishAutoFacts.resolve();
      await page.getByText(autoExplanation, { exact: true }).waitFor();
      await page.waitForFunction(() => window.durablePressGeometry !== null);
      const frames = await page.evaluate(() => window.durablePressGeometry);
      await page.mouse.up();
      for (const [phase, bounds] of Object.entries(frames))
        assert.deepEqual(
          bounds,
          before,
          `Late automatic-update facts must not move the pressed action during ${phase}`,
        );
    };
    await Promise.all([reloaded, press()]);
  } catch (error) {
    // Fixed facts only: never emit composer text, session storage contents, tokens or arbitrary server errors.
    const state = await page.evaluate(() => {
      const saved = sessionStorage.getItem("hz.update.receipt");
      let receipt = "absent";
      try {
        const stage = saved && JSON.parse(saved).stage;
        if (["uncertain", "installed", "reloading"].includes(stage)) receipt = stage;
        else if (saved) receipt = "invalid";
      } catch {
        receipt = "invalid";
      }
      return {
        receipt,
        modal: !!document.querySelector('[role="dialog"],[role="alertdialog"]'),
        terminal: !!document.querySelector(".xterm"),
        preservationBlocked: document.body.textContent.includes(
          "Close the terminal or editor before updating; its open work will stay here.",
        ),
      };
    });
    const composer = (await page.getByLabel("Message your Heinzel").count()) > 0;
    console.error(
      `DURABLE_UPDATE_STATE:${JSON.stringify({ ...state, composer, updated, restarted, requests: updateRequests })}`,
    );
    throw error;
  }
  await page.locator("#splash").waitFor({ state: "hidden", timeout: 15_000 });
  await page.waitForFunction(() => sessionStorage.getItem("hz.update.receipt") === null, null, { timeout: 15_000 });
  assert.equal(updateRequests.install, 1, "One physical gesture must install exactly once");
  assert.equal(updateRequests.restart, 1, "One physical gesture must restart exactly once");
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
  publishAutoFacts.resolve();
  await page.unrouteAll({ behavior: "wait" });
  await context.close();
  await browser.close();
}
