import assert from "node:assert/strict";
import test from "node:test";
import { lifecycle, parseConnection } from "../src/connection.js";

const valid = "--hz-url=http://127.0.0.1:8123/#token=abcdefghijklmnopqrstuvwxyz0123456789";

test("accepts only explicit authenticated loopback URLs and keeps token separate from origin", () => {
  const connection = parseConnection([valid]);
  assert.equal(connection.origin, "http://127.0.0.1:8123");
  assert.equal(connection.token, "abcdefghijklmnopqrstuvwxyz0123456789");
  for (const url of [
    "http://localhost:8123/#token=abcdefghijklmnopqrstuvwxyz0123456789",
    "https://127.0.0.1:8123/#token=abcdefghijklmnopqrstuvwxyz0123456789",
    "http://127.0.0.1:8123/#token=short",
    "http://127.0.0.1:8123/#token=abcdefghijklmnopqrstuvwxyz0123456789&other=x",
  ]) assert.throws(() => parseConnection([`--hz-url=${url}`]));
  assert.throws(() => parseConnection([]));
});

test("uses bearer auth on a same-origin lifecycle read and returns no credential", async () => {
  const connection = parseConnection([valid]);
  let request;
  const value = await lifecycle(connection, async (url, options) => {
    request = { url, options };
    return { ok: true, json: async () => ({ instance: "abc", version: "1.2.3", restartable: true }) };
  });
  assert.equal(request.url, "http://127.0.0.1:8123/lifecycle");
  assert.equal(request.options.headers.Authorization, "Bearer abcdefghijklmnopqrstuvwxyz0123456789");
  assert.deepEqual(value, { instance: "abc", version: "1.2.3", restartable: true });
  assert.equal(JSON.stringify(value).includes(connection.token), false);
});
