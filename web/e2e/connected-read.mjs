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
  await page.addInitScript(() => {
    const log = (window.__dlog = []);
    const t0 = performance.now();
    const at = () => Math.round(performance.now() - t0);
    const seen = new WeakSet();
    let n = 0;
    new MutationObserver(() => {
      for (const s of document.querySelectorAll("details > summary")) {
        if (s.textContent !== "Details" || seen.has(s.parentElement)) continue;
        const d = s.parentElement;
        const id = ++n;
        seen.add(d);
        log.push(`${at()} details#${id} new open=${d.open}`);
        d.addEventListener("toggle", (e) => log.push(`${at()} details#${id} toggle ${e.newState} open=${d.open}`));
        new MutationObserver(() => log.push(`${at()} details#${id} attr open=${d.open}`)).observe(d, { attributes: true, attributeFilter: ["open"] });
        const check = setInterval(() => { if (!d.isConnected) { log.push(`${at()} details#${id} gone`); clearInterval(check); } }, 20);
      }
    }).observe(document, { subtree: true, childList: true });
    document.addEventListener("click", (e) => log.push(`${at()} click ${e.target.nodeName}:${(e.target.textContent || "").slice(0, 16)}`), true);
  });
  await startChatJourney(page, url);
  const panel = page.getByRole("region", { name: "Connected read review" });
  await panel.getByLabel("Read operation").waitFor({ state: "visible", timeout: 10000 }).catch(async (error) => {
    const why = await panel.getByLabel("Read operation").evaluate((node) => {
      const chain = [];
      for (let el = node; el && el !== document.documentElement; el = el.parentElement) {
        const st = getComputedStyle(el);
        const r = el.getBoundingClientRect();
        chain.push({ tag: el.nodeName, cls: (el.className && String(el.className).slice(0, 60)) || "", hidden: el.hidden, inert: el.inert, open: el.nodeName === "DETAILS" ? el.open : undefined, display: st.display, vis: st.visibility, cv: st.contentVisibility, rect: [r.x, r.y, r.width, r.height].map(Math.round) });
      }
      return { checkVisibility: node.checkVisibility?.(), chain, log: window.__dlog };
    });
    throw new Error(`Read operation hidden: ${JSON.stringify(why)}\n${error}`);
  });
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
  await page.getByRole("button", { name: "Send" }).click();
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
