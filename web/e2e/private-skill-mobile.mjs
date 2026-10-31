// Real component/CSS in emulated phone browsers. All private API calls are intercepted locally.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { chromium, webkit } from "playwright-core";
import { createServer } from "vite";

const require = createRequire(import.meta.url);
const axe = readFileSync(require.resolve("axe-core/axe.min.js"), "utf8");
const session = "hz-01ARZ3NDEKTSV4RRFFQ69G5FAV";
const id = `draft-${"a".repeat(122)}`;
const sharingId = `sharing-${"b".repeat(120)}`;
const scope = `team-${"c".repeat(123)}`;
const filePath = `scripts/${"script".repeat(20)}.py`;
const fileText = `# Private instructions\n${"continuous-text".repeat(120)}`;
const draft = {
  session_id: session,
  request_id: id,
  name: "private-skill",
  requested_scope: "team",
  scope_ref: scope,
  files: { "SKILL.md": fileText, [filePath]: "print('private review only')" },
  status: "draft",
  preview_sha256: "a".repeat(64),
};
const server = await createServer({ server: { host: "127.0.0.1", port: 0 } });
await server.listen();
const address = server.httpServer.address();
assert(address && typeof address !== "string");
const results = [];
try {
  for (const [name, engine] of [
    ["chromium", chromium],
    ["webkit", webkit],
  ]) {
    const browser = await engine.launch();
    try {
      for (const width of [320, 390]) {
        const page = await browser.newPage({ viewport: { width, height: 750 }, isMobile: true, hasTouch: true });
        const calls = [];
        const pageErrors = [];
        page.on("pageerror", (error) => pageErrors.push(String(error)));
        await page.addInitScript((fixedId) => {
          crypto.randomUUID = () => fixedId;
        }, sharingId);
        await page.route(
          (url) => url.pathname.startsWith("/api/"),
          async (route) => {
            const request = route.request();
            const url = new URL(request.url());
            calls.push(`${request.method()} ${url.pathname}`);
            const query = url.searchParams;
            let body;
            if (request.method() === "GET" && url.pathname === "/api/heinzel/skill-drafts") {
              assert.equal(query.get("session_id"), session);
              body = { items: [{ request_id: id, seq: 1 }], next_cursor: null };
            } else if (request.method() === "GET" && url.pathname === "/api/heinzel/skill-draft") {
              assert.equal(query.get("session_id"), session);
              assert.equal(query.get("request_id"), id);
              body = draft;
            } else if (request.method() === "POST" && url.pathname === "/api/heinzel/propose-skill-sharing") {
              const payload = request.postDataJSON();
              assert.deepEqual(payload, {
                session_id: session,
                request_id: sharingId,
                draft_request_id: id,
                draft_sha256: "a".repeat(64),
              });
              body = {
                ...payload,
                requested_scope: "team",
                scope_ref: scope,
                status: "awaiting_review",
                authority: "unverified",
              };
            } else {
              throw new Error(`Unexpected API request: ${request.method()} ${url.pathname}`);
            }
            await route.fulfill({ status: 200, json: body });
          },
        );
        await page.goto(`http://127.0.0.1:${address.port}/e2e/private-skill-mobile.html`);
        await page.getByText("Skill draft", { exact: true }).click();
        await page.getByRole("button", { name: "Load saved drafts" }).click();
        await page.getByRole("button", { name: `Open draft ${id}` }).click();
        await page.getByText("Saved private draft: private-skill").waitFor();
        assert.equal(await page.locator("pre").first().textContent(), fileText);
        assert.equal(await page.getByText(filePath, { exact: true }).count(), 1);
        await page.getByRole("button", { name: "Request review of sharing" }).click();
        await page
          .getByRole("status")
          .getByText(/awaiting review; authority unverified\. Not shared/)
          .waitFor();
        await page.evaluate(axe);
        const violations = await page.evaluate(() =>
          window.axe.run(document, {
            runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"] },
          }),
        );
        assert.deepEqual(
          violations.violations.map((item) => item.id),
          [],
          `${name} ${width} accessibility`,
        );
        const overflow = await page.evaluate(() => ({
          viewport: document.documentElement.clientWidth,
          page: document.documentElement.scrollWidth,
        }));
        assert.ok(
          overflow.page <= overflow.viewport,
          `${name} ${width}: horizontal document overflow ${JSON.stringify(overflow)}`,
        );
        assert.deepEqual(pageErrors, []);
        assert.deepEqual(calls, [
          "GET /api/heinzel/skill-drafts",
          "GET /api/heinzel/skill-draft",
          "POST /api/heinzel/propose-skill-sharing",
        ]);
        results.push({ browser: name, width });
        await page.close();
      }
    } finally {
      await browser.close();
    }
  }
  console.log(JSON.stringify(results));
} finally {
  await server.close();
}
