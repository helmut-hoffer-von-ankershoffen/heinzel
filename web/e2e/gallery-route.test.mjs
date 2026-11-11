import assert from "node:assert/strict";
import test from "node:test";
import { guardGalleryRequest } from "./gallery-route.mjs";

const origin = "http://127.0.0.1:1234";
function request(url, fetch) {
  const result = { aborted: false, fulfilled: false };
  return {
    result,
    route: {
      request: () => ({ url: () => url, method: () => "GET" }),
      fetch,
      abort: async () => {
        result.aborted = true;
      },
      fulfill: async () => {
        result.fulfilled = true;
      },
    },
  };
}

test("a pending owned fetch may be disposed only during deliberate teardown", async () => {
  let close = false;
  let reject;
  const pending = new Promise((_, fail) => {
    reject = fail;
  });
  const { route } = request(`${origin}/api/state`, () => pending);
  const task = guardGalleryRequest(route, origin, [], () => close);
  close = true;
  reject(new Error("route.fetch: Request context disposed."));
  await task;
  await assert.rejects(
    guardGalleryRequest(
      request(`${origin}/api/state`, async () => {
        throw new Error("network failure");
      }).route,
      origin,
      [],
      () => true,
    ),
    /network failure/,
  );
  await assert.rejects(
    guardGalleryRequest(
      request(`${origin}/api/state`, async () => {
        throw new Error("route.fetch: Request context disposed.");
      }).route,
      origin,
      [],
      () => false,
    ),
    /Request context disposed/,
  );
});

test("external requests and local redirects remain recorded and blocked", async () => {
  const unexpected = [];
  const external = request("https://example.invalid/", () => assert.fail("must not fetch"));
  await guardGalleryRequest(external.route, origin, unexpected, () => false);
  assert.equal(external.result.aborted, true);
  const redirect = request(`${origin}/redirect`, async (options) => {
    assert.deepEqual(options, { maxRedirects: 0 });
    return { status: () => 302 };
  });
  await guardGalleryRequest(redirect.route, origin, unexpected, () => false);
  assert.equal(redirect.result.aborted, true);
  assert.deepEqual(unexpected, ["https://example.invalid/", "redirect /redirect"]);
});
