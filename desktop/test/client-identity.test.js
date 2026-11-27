import assert from "node:assert/strict";
import test from "node:test";
import { CLIENT_HEADER, attachClientHeader, clientHeader } from "../src/client-identity.js";

test("the header names flavor, versions and machine, quoting what needs it and saying unknown otherwise", () => {
  assert.equal(clientHeader({ appVersion: "0.9.54", backendVersion: "0.9.54", machine: "Demo's Air" }),
    "flavor=electron;app=0.9.54;backend=0.9.54;machine=Demo's%20Air");
  assert.equal(clientHeader({ appVersion: "1", machine: "" }), "flavor=electron;app=1;backend=unknown;machine=unknown");
});

test("only requests to the workspace origin carry the header, built from the current identity", () => {
  let filter; let handler; let backend;
  attachClientHeader({ webRequest: { onBeforeSendHeaders: (f, h) => { filter = f; handler = h; } } }, "https://mini.ts.net:8737",
    () => ({ appVersion: "2", backendVersion: backend, machine: "Air" }));
  assert.deepEqual(filter, { urls: ["<all_urls>"] });
  let sent;
  handler({ url: "https://mini.ts.net:8737/api/workspace-version", requestHeaders: { Accept: "x", Authorization: "Bearer fixture" } }, (result) => { sent = result.requestHeaders; });
  assert.equal(sent.Accept, "x");
  assert.equal(sent[CLIENT_HEADER], "flavor=electron;app=2;backend=unknown;machine=Air");
  backend = "0.9.54";
  handler({ url: "https://mini.ts.net:8737/api/info", requestHeaders: { Authorization: "Bearer fixture" } }, (result) => { sent = result.requestHeaders; });
  assert.match(sent[CLIENT_HEADER], /backend=0\.9\.54/);
  for (const url of ["https://elsewhere.invalid/api/info", "https://mini.ts.net/api/info", "https://mini.ts.net:8737/asset"]) {
    handler({ url, requestHeaders: { "x-heinzel-client": "old", Authorization: "Bearer fixture" } }, (result) => { sent = result.requestHeaders; });
    assert.equal(Object.keys(sent).some((key) => key.toLowerCase() === "x-heinzel-client"), false);
  }
  handler({ url: "https://mini.ts.net:8737/api/info", requestHeaders: { "X-Heinzel-Client": "forged", "x-heinzel-client": "old" } }, (result) => { sent = result.requestHeaders; });
  assert.equal(Object.keys(sent).some((key) => key.toLowerCase() === "x-heinzel-client"), false);
  handler({ url: "https://mini.ts.net:8737/api/info", requestHeaders: { authorization: "Bearer fixture", "x-heinzel-client": "old" } }, (result) => { sent = result.requestHeaders; });
  assert.equal(Object.keys(sent).filter((key) => key.toLowerCase() === "x-heinzel-client").length, 1);
  // Chromium invokes the same filter at the redirect destination: never forward the label off origin.
  handler({ url: "https://external.example/api/info", requestHeaders: sent }, (result) => { sent = result.requestHeaders; });
  assert.equal(Object.keys(sent).some((key) => key.toLowerCase() === "x-heinzel-client"), false);
});


test("fields are bounded by Unicode scalar count before encoding", () => {
  const machine = "𐐀 ".repeat(40);
  assert.equal(clientHeader({ machine }),
    `flavor=electron;app=unknown;backend=unknown;machine=${encodeURIComponent("𐐀 ".repeat(32))}`);
});
