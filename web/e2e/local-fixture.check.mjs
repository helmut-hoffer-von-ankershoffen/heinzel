import assert from "node:assert/strict";
import { createServer } from "node:http";
import { test } from "node:test";
import { guardFixturePage, requestUrl, startFixture } from "./local-fixture.mjs";

test("parses an origin-form API target without contacting the parsing base", () => {
  const url = requestUrl("/api/stream/system/daemon/restart?confirm=true");
  assert.equal(url.pathname, "/api/stream/system/daemon/restart");
  assert.equal(url.searchParams.get("confirm"), "true");
});

test("allows only the fixture origin and records blocked requests and page errors", async () => {
  let handler;
  let onError;
  const page = {
    on: (event, callback) => {
      assert.equal(event, "pageerror");
      onError = callback;
    },
    route: async (pattern, callback) => {
      assert.equal(pattern, "**/*");
      handler = callback;
    },
  };
  const { blocked, errors } = await guardFixturePage(page, "https://fixture.invalid:12345");
  const events = [];
  const route = (url) => ({
    request: () => ({ url: () => url }),
    continue: () => events.push("continued"),
    abort: () => events.push("aborted"),
  });
  handler(route("https://fixture.invalid:12345/path"));
  handler(route("https://example.invalid/"));
  onError(new Error("fixture error"));
  assert.deepEqual(events, ["continued", "aborted"]);
  assert.deepEqual(blocked, ["https://example.invalid/"]);
  assert.deepEqual(errors, ["Error: fixture error"]);
});

test("poisoned proxy port cannot reach a backend, including unexpected API paths", async () => {
  const previous = process.env.HZ_API_PORT;
  const hits = [];
  const sentinel = createServer((req, res) => {
    hits.push(req.url);
    res.writeHead(200).end("sentinel reached");
  });
  await new Promise((resolve) => sentinel.listen(0, "127.0.0.1", resolve));
  process.env.HZ_API_PORT = String(sentinel.address().port);
  let fixture;
  try {
    fixture = await startFixture((_req, _res, next) => next());
    const paths = ["/plugin-assets/x", "/plugin-sdk/x", "/theme-fonts/x", "/theme-assets/x", "/api/unexpected"];
    const statuses = [];
    for (const path of paths) {
      const response = await fetch(`${fixture.origin}${path}`);
      statuses.push(response.status);
    }
    assert.deepEqual(hits, [], "inherited proxy reached the sentinel backend");
    assert.deepEqual(
      statuses,
      paths.map(() => 403),
    );
    const transformed = await fetch(`${fixture.origin}/e2e/daemon-restart-proof.tsx`);
    assert.equal(transformed.status, 200);
    assert.match(await transformed.text(), /DaemonSettings/);
    assert.deepEqual(
      fixture.denied,
      paths.map((path) => `GET ${path}`),
    );
    assert.deepEqual(hits, []);
  } finally {
    if (fixture) await fixture.server.close();
    await new Promise((resolve, reject) => sentinel.close((error) => (error ? reject(error) : resolve())));
    if (previous === undefined) delete process.env.HZ_API_PORT;
    else process.env.HZ_API_PORT = previous;
  }
});
