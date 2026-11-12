// Real browser and streaming HTTP, synthetic backend, no external requests.
import assert from "node:assert/strict";
import { chromium, webkit } from "playwright-core";
import { guardFixturePage, requestUrl, startFixture } from "./local-fixture.mjs";

let active = null,
  restarts = 0,
  expert = false;
const unexpected = [];
const handler = (req, res, next) => {
  if (!req.url.startsWith("/api/")) return next();
  if (req.url === "/api/system/expert") {
    res.setHeader("Content-Type", "application/json");
    return res.end(JSON.stringify({ expert }));
  }
  if (req.url === "/api/system/daemon/status") {
    res.setHeader("Content-Type", "application/json");
    return res.end(
      JSON.stringify({
        platform: "darwin",
        installed: true,
        owned: true,
        loaded: false,
        paused: false,
        version: "1.2.3",
        installed_version: "1.2.3",
        version_mismatch: false,
        jobs: 3,
        failing: [],
        interval_minutes: 5,
        executable: "/fixture/hz",
        last_tick: null,
      }),
    );
  }
  if (requestUrl(req.url).pathname === "/api/stream/system/daemon/restart" && req.method === "POST") {
    restarts++;
    res.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-cache" });
    res.write(
      `event: phase\ndata: ${JSON.stringify({ id: "verify", label: "Checking the service", state: "running" })}\n\n`,
    );
    active = res;
    return;
  }
  unexpected.push(`${req.method} ${req.url}`);
  res.writeHead(500).end("unexpected request");
};
// Restart shows from the daemon status, the expert command line from a second request that can answer later. So count
// the command lines only once that answer is in, and when it is expected, once it is shown.
const expertAnswered = (page) => {
  const answered = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/system/expert");
  answered.catch(() => null); // a run that fails before the count reports its own error, not this open wait
  return answered;
};
async function commandLines(page, answered) {
  const command = page.getByText("hz system daemon restart", { exact: true });
  await answered;
  if (expert) await command.waitFor();
  return command.count();
}
const { server, origin, denied } = await startFixture(handler);
const outcomes = [];
try {
  for (const [name, engine] of [
    ["chromium", chromium],
    ["webkit", webkit],
  ]) {
    const browser = await engine.launch();
    try {
      for (const result of [true, false, "disconnected"]) {
        restarts = 0;
        active = null;
        expert = result === true;
        const context = await browser.newContext(),
          page = await context.newPage();
        const { blocked, errors } = await guardFixturePage(page, origin);
        await page.addInitScript(() => sessionStorage.setItem("hz.token", "fixture-token"));
        const answered = expertAnswered(page);
        await page.goto(`${origin}/e2e/daemon-restart-proof.html`);
        const restart = page.getByRole("button", { name: "Restart", exact: true });
        try {
          await restart.waitFor({ timeout: 10000 });
        } catch (error) {
          console.error(JSON.stringify({ text: await page.locator("body").textContent(), errors, unexpected }));
          throw error;
        }
        assert.equal(await commandLines(page, answered), expert ? 1 : 0);
        await restart.click();
        try {
          await page.getByText("Checking the service").waitFor({ timeout: 5000 });
        } catch (error) {
          console.error(
            JSON.stringify({ text: await page.locator("body").textContent(), errors, unexpected, restarts }),
          );
          throw error;
        }
        assert.equal(restarts, 1);
        assert.equal(await page.getByRole("button", { name: "Pause" }).isDisabled(), true);
        assert(active);
        if (result === "disconnected") active.end();
        else
          active.end(
            `event: phase\ndata: ${JSON.stringify({ id: "verify", label: "Checking the service", state: "done" })}\n\nevent: result\ndata: ${JSON.stringify({ data: { restarted: result } })}\n\n`,
          );
        if (result === true) await page.getByText("Restart sent. Waiting for the daemon's next check-in.").waitFor();
        else if (result === false)
          await page.getByText("The daemon was not restarted. Install it and try again.").waitFor();
        else {
          await page.getByText(/Restart unconfirmed/i).waitFor();
          assert.equal(await restart.isDisabled(), true);
          assert.equal(await page.getByRole("button", { name: "Try again" }).count(), 0);
          await page.getByText("Last reported: in progress").waitFor();
          await page.getByRole("button", { name: "Close" }).click();
          assert.equal(await restart.isEnabled(), true);
        }
        assert.equal(restarts, 1);
        assert.deepEqual(blocked, []);
        assert.deepEqual(errors, []);
        outcomes.push({ browser: name, result, restarts });
        await context.close();
      }
    } finally {
      await browser.close();
    }
  }
  assert.deepEqual(unexpected, []);
  assert.deepEqual(denied, []);
  console.log(JSON.stringify({ outcomes, unexpected, denied }));
} finally {
  active?.end();
  await server.close();
}
