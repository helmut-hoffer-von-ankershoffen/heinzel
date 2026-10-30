// Mounted component with local API fakes: no provider, queue, or native notifications are invoked.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { chromium, webkit } from "playwright-core";
import { createServer } from "vite";

const require = createRequire(import.meta.url);
const axe = readFileSync(require.resolve("axe-core/axe.min.js"), "utf8");
const session = "hz-01ARZ3NDEKTSV4RRFFQ69G5FAV";
const contract = {
  session_id: session,
  request_id: "contract",
  preview_sha256: "digest",
  timeout_seconds: 60,
  max_usd: null,
};
const grant = {
  request_id: "read-one",
  capability: "calendar",
  method: "events",
  review: {
    session,
    account_identity: "fixture-account",
    item_identity: { collection: ["events"], identity_field: "id", revision_field: "version" },
  },
};
const server = await createServer({ server: { host: "127.0.0.1", port: 0 } });
await server.listen();
const address = server.httpServer.address();
assert(address && typeof address !== "string");
const outcomes = [];
try {
  for (const [name, engine] of [
    ["chromium", chromium],
    ["webkit", webkit],
  ]) {
    const browser = await engine.launch();
    try {
      for (const width of [390, 1280]) {
        const page = await browser.newPage({ viewport: { width, height: 844 } });
        const errors = [];
        page.on("pageerror", (error) => errors.push(String(error)));
        let schedule = null;
        let status = "proposed";
        let action = null;
        const submissions = [];
        let confirms = 0;
        await page.route(
          (url) => url.pathname.startsWith("/api/"),
          async (route) => {
            const request = route.request();
            const url = new URL(request.url());
            const path = url.pathname;
            let body;
            let code = 200;
            if (path === "/api/heinzel/latest-schedule-suggestion-view") body = null;
            else if (path === "/api/heinzel/latest-scheduled-agent-proposal") body = contract;
            else if (path === "/api/heinzel/agent-contract-review") body = { proposal: contract, accepted: true };
            else if (path === "/api/heinzel/connected-read-state")
              body = { session_id: session, status: "approved", proposal: grant };
            else if (path === "/api/heinzel/scheduled-notification-attempts")
              body = { session_id: session, items: [], next_cursor: null };
            else if (path === "/api/heinzel/agent-schedules")
              body = { session_id: session, items: schedule ? [{ binding: schedule, status }] : [], next_cursor: null };
            else if (path === "/api/heinzel/propose-agent-schedule") {
              const payload = request.postDataJSON();
              submissions.push(payload);
              if (submissions.length === 1) {
                code = 503;
                body = { error: "lost acknowledgment" };
              } else {
                schedule = { ...payload, connected_grant: grant, contract_revision: 1 };
                body = schedule;
              }
            } else if (path === "/api/heinzel/agent-schedule") body = { binding: schedule, status };
            else if (path === "/api/system/runner/chat/propose-agent-schedule-review") {
              action = {
                id: "action",
                module: "heinzel",
                operation: "approve-agent-schedule",
                status: "pending",
                command: "Review schedule",
                arguments: { session_id: session, request_id: schedule.request_id, review: schedule },
              };
              body = { id: "chat", personal_session_id: session, actions: [action] };
            } else if (path === "/api/system/runner/chat/show")
              body = { id: "chat", personal_session_id: session, actions: [action] };
            else if (path === "/api/system/runner/chat/confirm") {
              confirms++;
              assert.equal(url.searchParams.get("session"), "chat");
              assert.equal(url.searchParams.get("action"), "action");
              status = "approved";
              action = { ...action, status: "completed" };
              body = { id: "chat", personal_session_id: session, actions: [action] };
            } else throw new Error(`Unexpected API ${request.method()} ${path}`);
            await route.fulfill({ status: code, json: body });
          },
        );
        await page.goto(`http://127.0.0.1:${address.port}/e2e/item-policy.html`);
        await page.getByText("Recurring agent turns", { exact: true }).click();
        const notice = page.getByRole("checkbox", { name: /Allow this agent to queue local notifications/ });
        assert.equal(await notice.isChecked(), false);
        await notice.check();
        assert.equal(
          await page
            .getByRole("option", { name: "Once per observed item revision" })
            .evaluate((option) => option.disabled),
          true,
        );
        const source = page.getByRole("checkbox", { name: /Allow the separately approved calendar.events read/ });
        await source.check();
        await page.getByLabel("Notification deduplication").selectOption("revision");
        await page.getByRole("button", { name: "Propose recurring turns" }).click();
        await page.getByRole("button", { name: "Retry same proposal" }).waitFor();
        await page.reload();
        await page.getByText("Recurring agent turns", { exact: true }).click();
        assert.equal(submissions.length, 1);
        assert.equal(await page.getByLabel("Notification deduplication").inputValue(), "revision");
        await page.getByRole("button", { name: "Retry same proposal" }).click();
        await page.getByRole("button", { name: "Review this schedule" }).waitFor();
        assert.equal(submissions.length, 2);
        assert.deepEqual(submissions[0], submissions[1]);
        assert.equal(submissions[0].connected_request_id, "read-one");
        assert.equal(submissions[0].notification_policy.item_evidence.deduplicate_by, "revision");
        assert.equal(confirms, 0);
        await page.getByText(/Provider-reported collection events/).waitFor();
        await page.getByRole("button", { name: "Review this schedule" }).click();
        const card = page.getByRole("heading", { name: "Review agent schedule" }).locator("..");
        assert.match((await card.textContent()) ?? "", /revision/);
        await page.evaluate(axe);
        const violations = await page.evaluate(() =>
          window.axe.run(document, {
            runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"] },
          }),
        );
        assert.deepEqual(
          violations.violations.map((item) => item.id),
          [],
        );
        assert.equal(
          await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth),
          false,
        );
        await card.getByRole("button", { name: "Confirm" }).click();
        await page.getByText(/Approved; the daemon must be running/).waitFor();
        assert.equal(confirms, 1);
        assert.deepEqual(errors, []);
        outcomes.push({ browser: name, width, retry: true, confirmed: true });
        await page.close();
      }
    } finally {
      await browser.close();
    }
  }
  console.log(JSON.stringify(outcomes));
} finally {
  await server.close();
}
