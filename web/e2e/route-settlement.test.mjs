import assert from "node:assert/strict";
import { test } from "vitest";
import { settleRoutes } from "./route-settlement.mjs";

function fixture() {
  const fetched = Promise.withResolvers();
  const held = Promise.withResolvers();
  const events = [];
  let disposed = false;
  const callback = (async () => {
    await fetched.promise;
    if (disposed) throw new Error("Request context disposed");
    events.push("fetched");
    await held.promise;
    if (disposed) throw new Error("Request context disposed");
    events.push("fulfilled");
  })();
  // Observe failure immediately, just as the driver does; assertions below consume it.
  const result = callback.then(
    () => null,
    (error) => error,
  );
  return {
    events,
    fetched,
    result,
    release: () => held.resolve(),
    close: () => {
      disposed = true;
      events.push("closed");
    },
    page: {
      unrouteAll: async (options) => {
        assert.deepEqual(options, { behavior: "wait" });
        events.push("unrouted");
        fetched.resolve();
        await callback;
      },
    },
  };
}

for (const phase of ["fetch", "held response"]) {
  test(`old close-before-settlement fails during ${phase}`, async () => {
    const route = fixture();
    if (phase === "held response") {
      route.fetched.resolve();
      await Promise.resolve();
    }
    route.close();
    route.fetched.resolve();
    route.release();
    assert.match((await route.result).message, /Request context disposed/);
  });

  test(`settlement preserves primary assertion during ${phase}`, async () => {
    const route = fixture();
    const primary = new Error("original pointer assertion");
    if (phase === "held response") {
      route.fetched.resolve();
      await Promise.resolve();
    }
    await assert.rejects(
      async () => {
        try {
          throw primary;
        } finally {
          try {
            await settleRoutes(route.page, route.release);
          } finally {
            route.close();
          }
        }
      },
      (error) => error === primary,
    );
    assert.equal(await route.result, null);
    assert.ok(route.events.indexOf("fulfilled") < route.events.indexOf("closed"));
  });
}

test("settlement surfaces route failures", async () => {
  const failure = new Error("upstream fetch failed");
  await assert.rejects(
    settleRoutes({
      unrouteAll: async () => {
        throw failure;
      },
    }),
    (error) => error === failure,
  );
});
