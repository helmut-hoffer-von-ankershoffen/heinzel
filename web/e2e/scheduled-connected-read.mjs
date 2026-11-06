import assert from "node:assert/strict";
import { join } from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { publishJson } from "./atomic-json.mjs";
import { openChatJourney, revealChat, runDaemonTurn } from "./chat-ready.mjs";
import { disclosureDiagnostics, watchDisclosure } from "./disclosure-diagnostics.mjs";
import { settleRoutes } from "./route-settlement.mjs";

const { axe, browser, context, page, token, url } = await openChatJourney();
const lateActivity = process.argv[6] === "late-activity";
const rolling = process.argv[5] === "rolling";
const lookbackStudy =
  process.env.HZ_WEBKIT_LOOKBACK_COUNTERFACTUAL === "1" &&
  process.platform === "linux" &&
  process.argv[3] === "webkit" &&
  process.argv[6] === "normal" &&
  rolling;
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
let rollingInputTrace;
let heldActivity;
const activityTrace = { release: "none", response: "none", fulfilledDuringFill: false };
let fillingLookback = false;

async function boundedActivityWait(promise, fallback) {
  const controller = new AbortController();
  try {
    return await Promise.race([promise, delay(5000, fallback, { signal: controller.signal })]);
  } finally {
    controller.abort();
  }
}

async function armLookbackActivity() {
  const session = new URL(page.url()).searchParams.get("chat");
  assert.ok(session, "The lookback study requires the mounted session");
  const ready = Promise.withResolvers();
  const release = Promise.withResolvers();
  let answerStarted = false;
  let claimed = false;
  const noteAnswer = (request) => {
    const target = new URL(request.url());
    if (
      target.origin === url.origin &&
      target.pathname === "/api/heinzel/answer-chat" &&
      request.method() === "POST" &&
      request.frame() === page.mainFrame()
    )
      answerStarted = true;
  };
  page.on("request", noteAnswer);
  heldActivity = {
    ready: ready.promise,
    stop() {
      page.off("request", noteAnswer);
      release.resolve("cleanup");
    },
  };
  await page.exposeBinding("hzReleaseLookbackActivity", ({ frame }, kind) => {
    if (frame === page.mainFrame() && fillingLookback && ["focus", "beforeinput", "input"].includes(kind))
      release.resolve(kind);
  });
  // The session refetch renders the required second answer before DurableSession invalidates tool activity.
  await page.route(
    (target) =>
      target.origin === url.origin &&
      target.pathname === "/api/heinzel/tool-activity" &&
      target.searchParams.get("session_id") === session,
    async (route) => {
      const request = route.request();
      if (claimed || !answerStarted || request.method() !== "GET" || request.frame() !== page.mainFrame()) {
        await route.continue();
        return;
      }
      claimed = true;
      page.off("request", noteAnswer);
      try {
        const response = await route.fetch({ timeout: 5000, maxRedirects: 0 });
        activityTrace.response = response.ok() ? "held" : "non-success";
        ready.resolve(response.ok());
        if (response.ok()) activityTrace.release = await boundedActivityWait(release.promise, "hold-timeout");
        await route.fulfill({ response });
        activityTrace.fulfilledDuringFill = fillingLookback;
        if (response.ok()) activityTrace.response = "forwarded";
      } catch {
        activityTrace.response = "route-error";
        ready.resolve(false);
        await route.abort().catch(() => {});
      }
    },
  );
}

const rollingCheckpoint = (kind) =>
  page.evaluate((kind) => window.__hzRollingInputInteraction?.checkpoint(kind), kind).catch(() => {});
const finishRollingInputTrace = (afterPrepare) =>
  page
    .evaluate((afterPrepare) => {
      const observer = window.__hzRollingInputInteraction;
      if (!observer) return [];
      try {
        if (afterPrepare) observer.checkpoint("after-prepare");
        return observer.records;
      } finally {
        observer.stop();
      }
    }, afterPrepare)
    .catch(() => []);
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
  if (lookbackStudy) await armLookbackActivity();
  await page.getByRole("button", { name: "Queue message" }).click();
  await page.getByText("Suggest an interval for this agent.").waitFor();
  await page.getByText("Read complete.").nth(1).waitFor({ timeout: 30000 });
  const panel = page.getByRole("region", { name: "Connected read review" });
  await panel.getByLabel("Read operation").selectOption("calendar.events");
  if (rolling) {
    let reply = null;
    if (lookbackStudy) {
      assert.equal(await boundedActivityWait(heldActivity.ready, false), true, "No genuine activity response was held");
      reply = await page.getByText("Read complete.").nth(1).locator("..").elementHandle({ timeout: 5000 });
    }
    await panel.getByRole("checkbox", { name: /Use a rolling window/ }).check();
    // Observe only the two numeric terms without changing focus, validation or the physical interaction.
    await panel
      .evaluate((region, reply) => {
        const controller = new AbortController();
        const records = [];
        const fields = [
          { field: "lookback", label: "Lookback seconds", expected: "60" },
          { field: "lookahead", label: "Lookahead seconds", expected: "600" },
        ];
        const observedField = (input) => {
          if (!(input instanceof HTMLInputElement) || input.type !== "number" || !region.contains(input)) return;
          const label = input.closest("label")?.textContent?.trim();
          return fields.find((field) => field.label === label);
        };
        const valueKind = (input, expected) => {
          if (!input || input.value === "") return "empty";
          if (input.value === expected) return "expected-fixture";
          return Number.isInteger(input.valueAsNumber) ? "other-integer" : "noninteger";
        };
        const candidates = () => [...region.querySelectorAll('input[type="number"]')];
        const original = new Map();
        if (reply) {
          for (const input of candidates()) {
            const field = observedField(input);
            if (field) original.set(field.field, input);
          }
        }
        const replyHasActivity = () =>
          [...reply.querySelectorAll("details > summary")].some((node) =>
            node.textContent?.trim().startsWith("Tool activity for this reply ·"),
          );
        const state = ({ field, expected }, input) => ({
          field,
          value: valueKind(input, expected),
          valid: Boolean(input?.validity.valid),
          focused: Boolean(input && document.activeElement === input),
          connected: Boolean(input?.isConnected),
          ...(reply && { sameNode: Boolean(input && original.get(field) === input) }),
        });
        const record = (kind, inputs) => {
          if (controller.signal.aborted) return;
          records.push({ kind, inputs, ...(reply && { replyHasActivity: replyHasActivity() }) });
          if (records.length > 32) records.shift();
        };
        const checkpoint = (kind) => {
          const current = candidates();
          const inputs = fields.map((field) => {
            const input = current.find((input) => observedField(input) === field);
            return state(field, input);
          });
          record(kind, inputs);
        };
        const activityObserver =
          reply &&
          new MutationObserver(() => {
            if (replyHasActivity()) {
              checkpoint("reply-activity-rendered");
              activityObserver.disconnect();
            }
          });
        activityObserver?.observe(reply, { childList: true, subtree: true, characterData: true });
        window.__hzRollingInputInteraction = {
          records,
          checkpoint,
          stop() {
            controller.abort();
            activityObserver?.disconnect();
          },
        };
        checkpoint("armed");
        for (const kind of ["beforeinput", "input", "change", "blur", ...(reply ? ["focus"] : [])]) {
          document.addEventListener(
            kind,
            (event) => {
              const field = observedField(event.target);
              if (!field) return;
              record(kind, [state(field, event.target)]);
              if (!reply || field.field !== "lookback" || !["focus", "beforeinput", "input"].includes(kind)) return;
              void window.hzReleaseLookbackActivity(kind).catch(() => {});
              if (kind !== "focus") {
                queueMicrotask(() => checkpoint(`${kind}-microtask`));
                requestAnimationFrame(() => checkpoint(`${kind}-frame`));
              }
            },
            { capture: true, passive: true, signal: controller.signal },
          );
        }
      }, reply)
      .catch(() => {});
    if (lookbackStudy) {
      const armed = await page.evaluate(() => window.__hzRollingInputInteraction?.records[0]);
      assert.ok(
        armed?.replyHasActivity === false && armed.inputs.every(({ sameNode }) => sameNode),
        "The two original inputs must be mounted and the held activity absent before filling",
      );
    }
    fillingLookback = true;
    try {
      await panel.getByLabel("Lookback seconds").fill("60");
    } finally {
      fillingLookback = false;
    }
    await rollingCheckpoint("after-lookback-fill");
    await panel.getByLabel("Lookahead seconds").fill("600");
    await rollingCheckpoint("after-lookahead-fill");
    await panel.getByLabel("Display timezone (IANA)").fill("Europe/Berlin");
    await rollingCheckpoint("after-timezone-fill");
    await panel.getByLabel("Fixed arguments (JSON)").fill("{}");
    await rollingCheckpoint("after-arguments-fill");
  } else {
    await panel
      .getByLabel("Fixed arguments (JSON)")
      .fill(JSON.stringify({ window: { since: "2026-01-01T00:00:00Z", until: "2026-01-02T00:00:00Z" } }));
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
    try {
      window.__hzRollingInputInteraction?.checkpoint("before-prepare");
    } catch {
      /* A diagnostic must not prevent the original click. */
    }
  });
  try {
    try {
      await prepare.click();
    } finally {
      if (rolling) rollingInputTrace = await finishRollingInputTrace(true);
    }
    await panel.getByText("Account: fixture-account").waitFor();
  } finally {
    await page.evaluate(() => window.__hzPrepareInteraction?.stop()).catch(() => {});
  }
  if (lookbackStudy) {
    assert.ok(["focus", "beforeinput", "input"].includes(activityTrace.release), "The fill must release the response");
    assert.equal(activityTrace.response, "forwarded");
    assert.ok(
      rollingInputTrace.some(({ kind }) => kind === "reply-activity-rendered"),
      "The held activity must render",
    );
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
  const rollingInputs = rollingInputTrace ?? (rolling ? await finishRollingInputTrace(false) : []);
  rollingInputTrace = rollingInputs;
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
  const diagnostic = `${message}\n${visible}\nLayout: ${JSON.stringify(layout)}\nDisclosure trace: ${JSON.stringify(disclosures)}\nRevocation trace: ${JSON.stringify(revocations.slice(-12))}\nPrepare interaction: ${JSON.stringify(prepareInteraction)}\nRolling input interaction: ${JSON.stringify(rollingInputs)}\nProposal trace: ${JSON.stringify(proposalTrace)}\nProposal form: ${JSON.stringify(form)}`;
  process.stderr.write(`${diagnostic.replaceAll(decodeURIComponent(token), "<redacted-session>")}\n`);
  process.exitCode = 1;
} finally {
  try {
    heldActivity?.stop();
    await settleRoutes(page, latePage?.release);
    if (lookbackStudy)
      publishJson(join(process.argv[4], "lookback-counterfactual.json"), {
        activity: activityTrace,
        inputs: rollingInputTrace ?? [],
      });
  } finally {
    try {
      await context.close();
    } finally {
      await browser.close();
    }
  }
}
