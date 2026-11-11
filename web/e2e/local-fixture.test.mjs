import assert from "node:assert/strict";
import { test } from "node:test";
import { guardFixturePage, requestUrl } from "./local-fixture.mjs";

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
