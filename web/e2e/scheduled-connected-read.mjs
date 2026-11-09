import assert from "node:assert/strict";
import { join } from "node:path";
import { openChatJourney, revealChat, runDaemonTurn } from "./chat-ready.mjs";
import { disclosureDiagnostics, watchDisclosure } from "./disclosure-diagnostics.mjs";
import { settleRoutes } from "./route-settlement.mjs";

const { axe, browser, context, page, token, url } = await openChatJourney();
const lateActivity = process.argv[6] === "late-activity";
if (lateActivity) {
  // Reproduce the hosted ordering: page DOM growth is committed between press/release, while its resize
  // notification arrives after release. Keep actual browser layout, response data and physical pointer events.
  await page.addInitScript(() => {
    const NativeObserver = window.ResizeObserver;
    const pending = new Map();
    window.__hzHoldPageResize = false;
    window.__hzReleasePageResize = () => {
      window.__hzHoldPageResize = false;
      for (const [observer, deliver] of pending) {
        pending.delete(observer);
        deliver();
      }
    };
    window.ResizeObserver = class extends NativeObserver {
      constructor(callback) {
        super((entries, observer) => {
          if (window.__hzHoldPageResize && entries.some(({ target }) => target.querySelector("#content")))
            pending.set(observer, () => callback(entries, observer));
          else callback(entries, observer);
        });
      }
      disconnect() {
        pending.delete(this);
        super.disconnect();
      }
    };
  });
}
// Only fixed stages, schema predicates and status categories: never request IDs, tokens or provider payloads.
const proposalTrace = [];
const proposalPaths = new Map([
  ["/api/heinzel/propose-rolling-connected-read", "rolling"],
  ["/api/heinzel/propose-connected-read", "fixed"],
  ["/api/heinzel/prepare-suggested-source", "suggested"],
]);
const traceProposal = (event) => {
  proposalTrace.push(event);
  if (proposalTrace.length > 16) proposalTrace.shift();
};
page.on("request", (request) => {
  const stage = proposalPaths.get(new URL(request.url()).pathname);
  if (!stage || request.method() !== "POST") return;
  let body;
  try {
    body = request.postDataJSON();
  } catch {
    /* Record no arbitrary parse output. */
  }
  traceProposal({
    kind: "request",
    stage,
    windowPolicy: !!body?.window_policy,
    fixedWindow: Object.hasOwn(body?.fixed_arguments ?? {}, "window"),
    validLookback: Number.isInteger(body?.window_policy?.lookback_seconds),
    validLookahead: Number.isInteger(body?.window_policy?.lookahead_seconds),
    expectedTimezone: body?.window_policy?.timezone === "Europe/Berlin",
  });
});
page.on("response", async (response) => {
  const stage = proposalPaths.get(new URL(response.url()).pathname);
  if (!stage || response.request().method() !== "POST") return;
  let category = response.ok() ? "success" : "http-error";
  if (!response.ok()) {
    try {
      const body = await response.json();
      if (typeof body?.error === "string" && body.error.includes("This session has another writer"))
        category = "busy-writer";
      else if (Array.isArray(body?.detail)) category = "schema-validation";
    } catch {
      category = "unreadable-error";
    }
  }
  traceProposal({ kind: "response", stage, status: response.status(), category });
});
const revocations = [];
let latePage;
const revocationPath = "/api/heinzel/revoke-connected-read";
page.on("request", (request) => {
  if (new URL(request.url()).pathname === revocationPath && request.method() === "POST")
    revocations.push({ kind: "request" });
});
page.on("response", (response) => {
  if (new URL(response.url()).pathname === revocationPath && response.request().method() === "POST")
    revocations.push({ kind: "response", status: response.status() });
});
try {
  await page.goto(url.href);
  await page.locator("#splash").waitFor({ state: "hidden", timeout: 30000 });
  const setup = page.getByRole("dialog");
  await setup.waitFor({ state: "visible", timeout: 5000 }).catch(() => {});
  if (await setup.isVisible().catch(() => false)) await setup.getByRole("button", { name: "Close setup" }).click();
  if (!(await page.getByLabel("Your message").isVisible())) await revealChat(page);
  await page.getByLabel("Your message").fill("Read my selected source.");
  await page.getByRole("button", { name: "Create a Heinzel and take this message" }).click();
  await page.getByRole("button", { name: "Queue message" }).click();
  await page.getByText("Read complete.").waitFor({ timeout: 30000 });
  const terms = page.getByRole("region", { name: "Proposed Heinzel terms" });
  await terms.getByRole("button", { name: "Review these terms" }).click();
  await terms.getByRole("button", { name: "Confirm" }).click();
  await terms.getByText("These exact terms were accepted.").waitFor();
  await page.getByLabel("Message your Heinzel").fill("Suggest an interval for this agent.");
  await page.getByRole("button", { name: "Queue message" }).click();
  await page.getByText("Suggest an interval for this agent.").waitFor();
  await page.getByText("Read complete.").nth(1).waitFor({ timeout: 30000 });
  const panel = page.getByRole("region", { name: "Connected read review" });
  // The review form is controlled: a value counts only once the app has rendered it back. Fill each field and wait
  // until a rendered frame still shows it, so the press below submits what the form holds, not a value in flight.
  const rendered = () =>
    page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  const entered = async (label, value) => {
    const field = panel.getByLabel(label);
    await field.fill(value);
    await rendered();
    assert.equal(await field.inputValue(), value, `${label} must keep the entered value after a render`);
  };
  await panel.getByLabel("Read operation").selectOption("calendar.events");
  if (process.argv[5] === "rolling") {
    const rollingChoice = panel.getByRole("checkbox", { name: /Use a rolling window/ });
    await rollingChoice.check();
    await rendered();
    assert.equal(await rollingChoice.isChecked(), true, "the rolling window choice must stay on after a render");
    await entered("Lookback seconds", "60");
    await entered("Lookahead seconds", "600");
    await entered("Display timezone (IANA)", "Europe/Berlin");
    await entered("Fixed arguments (JSON)", "{}");
  } else {
    await entered(
      "Fixed arguments (JSON)",
      JSON.stringify({ window: { since: "2026-01-01T00:00:00Z", until: "2026-01-02T00:00:00Z" } }),
    );
  }
  const prepare = panel.getByRole("button", { name: "Prepare review" });
  await prepare.evaluate((button) => {
    const controller = new AbortController();
    const records = [];
    const region = button.closest("section");
    const select = region?.querySelector("select");
    const snapshot = (kind, event) => {
      const { x, y, width, height } = button.getBoundingClientRect();
      const hit = event && document.elementFromPoint(event.clientX, event.clientY);
      records.push({
        kind,
        trusted: event?.isTrusted ?? null,
        targetInside: event ? event.composedPath().includes(button) : null,
        pointInside: event ? Boolean(hit && button.contains(hit)) : null,
        disabled: button.disabled,
        connected: button.isConnected,
        selectedOption: Boolean(select?.value && select.selectedOptions.length),
        rect: [x, y, width, height].map((value) => Math.round(value * 100) / 100),
        scroll: [button.closest(".overflow-y-auto")?.scrollTop, button.closest(".workspace-scroll")?.scrollTop],
      });
      if (records.length > 12) records.shift();
    };
    window.__hzPrepareInteraction = {
      records,
      stop() {
        snapshot("after");
        controller.abort();
      },
    };
    snapshot("before");
    for (const kind of ["pointerdown", "pointerup", "click"]) {
      document.addEventListener(kind, (event) => snapshot(kind, event), {
        capture: true,
        passive: true,
        signal: controller.signal,
      });
    }
  });
  try {
    await prepare.click();
    // The proposal either appears or the form says it could not be prepared: fail on the alert at once, with the values
    // the form held, instead of waiting out the locator timeout.
    const refused = panel.getByRole("alert").filter({ hasText: "The action could not be verified" });
    await panel.getByText("Account: fixture-account").or(refused).first().waitFor();
    if (await refused.isVisible()) {
      const held = await panel.evaluate((node) =>
        [...node.querySelectorAll("input, textarea, select")].map((field) => [
          field.closest("label")?.textContent?.trim().slice(0, 40) ?? field.tagName,
          field.type === "checkbox" ? field.checked : field.value,
        ]),
      );
      assert.fail(`Prepare review was refused; the form held ${JSON.stringify(held)}`);
    }
  } finally {
    await page.evaluate(() => window.__hzPrepareInteraction?.stop()).catch(() => {});
  }
  await panel.getByText("Provider-reported candidate read: Candidate metadata may be filtered locally.").waitFor();
  await panel.getByRole("button", { name: "Review access" }).click();
  await panel.getByRole("heading", { name: "Review connected read" }).waitFor();
  await page.evaluate(axe);
  const violations = await panel.evaluate(async (node) => (await window.axe.run(node)).violations.map(({ id }) => id));
  assert.deepEqual(violations, []);
  await panel.getByRole("button", { name: "Confirm" }).click();
  await panel.getByText(/Recorded state: approved/).waitFor();
  const schedule = page.locator("details").filter({ has: page.getByText("Recurring Heinzel turns", { exact: true }) });
  await watchDisclosure(schedule.getByText("Recurring Heinzel turns", { exact: true }), "recurring-turns");
  await schedule.getByText("Recurring Heinzel turns", { exact: true }).click();
  await schedule.getByText(/Suggested interval: every 7 minutes/).waitFor();
  assert.equal(await schedule.getByLabel("Every (minutes)").inputValue(), "5");
  await schedule.getByRole("button", { name: "Use interval in preview" }).click();
  assert.equal(await schedule.getByLabel("Every (minutes)").inputValue(), "7");
  const consent = await schedule.getByRole("checkbox", { name: /Allow the separately approved calendar.events/ });
  assert.equal(await consent.isChecked(), false);
  await consent.check();
  await schedule.getByLabel("Requests per turn").fill("4");
  await schedule.getByRole("button", { name: "Propose recurring turns" }).click();
  await schedule.getByRole("button", { name: "Review this schedule" }).click();
  await schedule.getByText("Provider-reported candidate read: Candidate metadata may be filtered locally.").waitFor();
  const card = schedule.getByRole("heading", { name: "Review Heinzel schedule" }).locator("../..");
  assert.match(await card.innerText(), /420/);
  assert.match(await card.innerText(), /fixture-account/);
  assert.match(await card.innerText(), /fixture-read/);
  if (process.argv[5] === "rolling") assert.match(await card.innerText(), /rolling-window/);
  assert.doesNotMatch(await card.innerText(), /approval_proof/);
  await page.evaluate(axe);
  const scheduleViolations = await schedule.evaluate(async (node) =>
    (await window.axe.run(node)).violations.map(({ id }) => id),
  );
  assert.deepEqual(scheduleViolations, []);
  await schedule.getByRole("button", { name: "Confirm" }).click();
  await schedule.getByText(/approved; the daemon must be running/i).waitFor();
  const session = new URLSearchParams(page.url().split("?")[1]).get("chat");
  assert.ok(session);
  if (process.argv[6] === "late-page" || lateActivity) {
    const ready = Promise.withResolvers();
    const release = Promise.withResolvers();
    const inserted = Promise.withResolvers();
    await page.route("**/api/system/doctor", async (route) => {
      const response = await route.fetch();
      ready.resolve();
      await release.promise;
      await route.fulfill({ response });
      inserted.resolve();
    });
    latePage = { ready: ready.promise, release: release.resolve, inserted: inserted.promise };
  }
  await runDaemonTurn(page, process.argv[4], session);
  const activity = page.getByText(/Tool activity for this reply/).nth(2);
  await watchDisclosure(activity, "daemon-activity");
  if (lateActivity) {
    await latePage.ready;
    await activity.hover();
    const box = await activity.boundingBox();
    assert.ok(box);
    const pointer = { x: box.x + box.width / 2, y: box.y + box.height / 2 };
    const priorHeight = await page.locator("#content").evaluate((node) => node.getBoundingClientRect().height);
    await page.evaluate(() => {
      window.__hzHoldPageResize = true;
    });
    try {
      await page.mouse.down();
      latePage.release();
      await latePage.inserted;
      await page.waitForFunction(
        (height) => document.getElementById("content").getBoundingClientRect().height > height,
        priorHeight,
      );
      const stillUnderPointer = await activity.evaluate(
        (node, { x, y }) => node.contains(document.elementFromPoint(x, y)),
        pointer,
      );
      await page.mouse.up();
      assert.equal(stillUnderPointer, true, "late page loading keeps the pressed disclosure under the pointer");
    } finally {
      await page.evaluate(() => window.__hzReleasePageResize());
    }
  } else await activity.click();
  await page
    .getByText(/connected_read · completed/)
    .first()
    .waitFor({ timeout: 30000 });
  await page.getByText("Approved read: first recorded result.").waitFor();
  assert.equal(await page.getByText("Approved read: first recorded result.").count(), 1);
  await page.getByText(/this is not event deduplication or complete history/i).waitFor();
  const revoke = panel.getByRole("button", { name: "Revoke this grant" });
  if (latePage && !lateActivity) {
    await latePage.ready;
    await revoke.hover();
    const priorHeight = await page.locator("#content").evaluate((node) => node.getBoundingClientRect().height);
    // Deliver the real pending page report between the same physical press and release as a normal click.
    await page.mouse.down();
    latePage.release();
    await latePage.inserted;
    await page.waitForFunction(
      (height) => document.getElementById("content").getBoundingClientRect().height > height,
      priorHeight,
    );
    await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    await page.mouse.up();
  } else await revoke.click();
  await panel.getByText(/Recorded state: revoked/).waitFor();
  assert.deepEqual(revocations, [{ kind: "request" }, { kind: "response", status: 200 }]);
  const saved = await page.evaluate(async (session) => {
    const credential = sessionStorage.getItem("hz.token") ?? localStorage.getItem("hz.device");
    const response = await fetch(`/api/heinzel/connected-read-state?${new URLSearchParams({ session_id: session })}`, {
      headers: { Authorization: `Bearer ${credential}` },
    });
    return { status: response.status, grant: (await response.json()).status };
  }, session);
  assert.deepEqual(saved, { status: 200, grant: "revoked" });
  await page.reload();
  await revealChat(page);
  await watchDisclosure(schedule.getByText("Recurring Heinzel turns", { exact: true }), "recurring-turns-after-reload");
  await schedule.getByText("Recurring Heinzel turns", { exact: true }).click();
  await schedule.getByText(/stale · contract revision/).waitFor();
  assert.equal(await panel.evaluate((node) => node.scrollWidth > node.clientWidth), false);
  console.log(JSON.stringify({ accepted: true }));
} catch (error) {
  const message = error instanceof Error ? (error.stack ?? error.message) : String(error);
  const visible = await page
    .locator("body")
    .innerText()
    .catch(() => "");
  const layout = await page
    .evaluate(() => {
      const summary = [...document.querySelectorAll("summary")].find(
        (node) => node.textContent === "Recurring Heinzel turns",
      );
      const ancestors = [];
      for (let node = summary; node; node = node.parentElement) {
        const box = node.getBoundingClientRect();
        const style = getComputedStyle(node);
        ancestors.push({
          tag: node.tagName,
          className: node.className,
          hidden: node.hidden,
          inert: node.inert,
          display: style.display,
          visibility: style.visibility,
          width: box.width,
          height: box.height,
          ariaHidden: node.getAttribute("aria-hidden"),
        });
      }
      return {
        ancestors,
        tabs: [...document.querySelectorAll('[role="tab"]')].map((node) => ({
          text: node.textContent,
          selected: node.getAttribute("aria-selected"),
        })),
      };
    })
    .catch(() => null);
  await page.screenshot({ path: join(process.argv[4], `scheduled-${process.argv[3]}-failure.png`) }).catch(() => {});
  const form = await page
    .getByRole("region", { name: "Connected read review" })
    .evaluate((node) => {
      const inputs = [...node.querySelectorAll("input")];
      const textarea = node.querySelector("textarea");
      let fixedObject = false;
      try {
        const value = JSON.parse(textarea?.value ?? "");
        fixedObject = !!value && typeof value === "object" && !Array.isArray(value);
      } catch {
        /* Fixed predicate only. */
      }
      return {
        rolling: inputs.some((input) => input.type === "checkbox" && input.checked),
        numericTerms: inputs
          .filter((input) => input.type === "number")
          .map((input) => input.value !== "" && Number.isInteger(Number(input.value))),
        expectedTimezone: inputs.some((input) => input.value === "Europe/Berlin"),
        fixedObject,
      };
    })
    .catch(() => null);
  const disclosures = await disclosureDiagnostics(page);
  const prepareInteraction = await page.evaluate(() => window.__hzPrepareInteraction?.records ?? []).catch(() => []);
  const diagnostic = `${message}\n${visible}\nLayout: ${JSON.stringify(layout)}\nDisclosure trace: ${JSON.stringify(disclosures)}\nRevocation trace: ${JSON.stringify(revocations.slice(-12))}\nPrepare interaction: ${JSON.stringify(prepareInteraction)}\nProposal trace: ${JSON.stringify(proposalTrace)}\nProposal form: ${JSON.stringify(form)}`;
  process.stderr.write(`${diagnostic.replaceAll(decodeURIComponent(token), "<redacted-session>")}\n`);
  process.exitCode = 1;
} finally {
  try {
    await settleRoutes(page, latePage?.release);
  } finally {
    try {
      await context.close();
    } finally {
      await browser.close();
    }
  }
}
