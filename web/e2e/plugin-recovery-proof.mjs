// Actual component and lifecycle client in fresh browsers, with only a synthetic backend.
import assert from "node:assert/strict";
import { chromium, webkit } from "playwright-core";
import { guardFixturePage, requestUrl, startFixture } from "./local-fixture.mjs";

const id = "z".repeat(43);
const candidate = {
  spec: "git+https://github.com/acme-example/heinzel-crm@v1.2.0",
  requirement: `heinzel-crm @ git+https://github.com/acme-example/heinzel-crm@${"b".repeat(40)}`,
  distribution: "heinzel-crm",
  version: "1.2.0",
  summary: "CRM notes.",
  source: "git",
  url: "https://github.com/acme-example/heinzel-crm",
  publisher: "Example",
  first_party: false,
  private: true,
  revision: "b".repeat(40),
  installed_version: null,
  warnings: [],
  also: "",
};
const result = {
  action: "add",
  done: true,
  distribution: "heinzel-crm",
  candidate,
  plugins: [],
  restart_required: true,
  next: [],
  operation_id: id,
};
let checks = 0,
  restartPosts = 0,
  writes = 0;
const unexpected = [];
const handler = (req, res, next) => {
  const url = requestUrl(req.url);
  if (!url.pathname.startsWith("/api/")) return next();
  res.setHeader("Content-Type", "application/json");
  const send = (body, status = 200) => {
    res.statusCode = status;
    res.end(JSON.stringify(body));
  };
  if (url.pathname === "/api/system/plugin/list") return send([]);
  if (url.pathname === "/api/system/plugin/pending") return send({ restart_required: false, changes: [] });
  if (url.pathname === "/api/system/plugin/search") return send({ plugins: [], warnings: [] });
  if (url.pathname === "/api/system/plugin/operation")
    return send({ operation_id: id, status: "completed", candidate, result });
  if (url.pathname === "/api/lifecycle") return send({ instance: "replacement", version: "0.9.23", restartable: true });
  if (url.pathname === "/api/lifecycle/verify") {
    checks++;
    return checks === 1
      ? send({ error: "fixture verification failed" }, 403)
      : send({ instance: "replacement", version: "0.9.23", matches: true, mismatched: [] });
  }
  if (url.pathname === "/api/restart") {
    restartPosts++;
    return send({ error: "unexpected restart" }, 500);
  }
  if (url.pathname.includes("/plugin/add")) {
    writes++;
    return send({ error: "unexpected write" }, 500);
  }
  unexpected.push(`${req.method} ${req.url}`);
  return send({ error: "unexpected request" }, 500);
};
const { server, origin, denied } = await startFixture(handler);
const outcomes = [];
try {
  for (const [name, engine] of [
    ["chromium", chromium],
    ["webkit", webkit],
  ]) {
    checks = 0;
    restartPosts = 0;
    writes = 0;
    const browser = await engine.launch(),
      context = await browser.newContext(),
      page = await context.newPage();
    let guards = { blocked: [], errors: [] };
    try {
      guards = await guardFixturePage(page, origin);
      const { blocked, errors } = guards;
      await page.addInitScript(
        ({ id }) => {
          sessionStorage.setItem("hz.token", "fixture-token");
          if (sessionStorage.getItem("proof-seeded")) return;
          sessionStorage.setItem("proof-seeded", "yes");
          sessionStorage.setItem("hz.plugin-add-operation.v1", id);
          sessionStorage.setItem(
            "hz.plugin-add-restart.v1",
            JSON.stringify({
              operation_id: id,
              acknowledged: true,
              observation: { retired: ["old"], version: "0.9.23", settings: [] },
            }),
          );
        },
        { id },
      );
      await page.goto(`${origin}/e2e/plugin-recovery-proof.html`);
      await page.getByRole("alert").getByText("fixture verification failed").waitFor({ timeout: 10000 });
      assert.equal(checks, 1);
      assert.equal(await page.getByRole("button", { name: "Install", exact: true }).count(), 0);
      assert.equal(await page.getByRole("button", { name: "Restart and apply" }).count(), 0);
      assert.ok(await page.evaluate(() => sessionStorage.getItem("hz.plugin-add-restart.v1")));
      await page.reload();
      await page.locator('body[data-fixture-reloaded="yes"]').waitFor({ timeout: 10000 });
      assert.equal(checks, 2);
      assert.equal(restartPosts, 0);
      assert.equal(writes, 0);
      assert.deepEqual(blocked, []);
      assert.deepEqual(errors, []);
      outcomes.push({ browser: name, failedVerification: true, reloadResumed: true, checks, restartPosts, writes });
    } catch (error) {
      console.error(
        JSON.stringify({
          browser: name,
          text: await page.locator("body").textContent(),
          checks,
          restartPosts,
          writes,
          unexpected,
          errors: guards.errors,
        }),
      );
      throw error;
    } finally {
      await context.close();
      await browser.close();
    }
  }
  assert.deepEqual(unexpected, []);
  assert.deepEqual(denied, []);
  console.log(JSON.stringify({ outcomes, unexpected, denied }));
} finally {
  await server.close();
}
