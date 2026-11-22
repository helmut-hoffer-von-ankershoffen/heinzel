import assert from "node:assert/strict";
import test from "node:test";
import { CLIENT_HEADER, attachClientHeader, clientHeader } from "../src/client-identity.js";

test("the header names flavor, versions and machine, quoting what needs it and saying unknown otherwise", () => {
  assert.equal(clientHeader({ appVersion: "0.9.54", backendVersion: "0.9.54", machine: "Helmut's Air" }),
    "flavor=electron;app=0.9.54;backend=0.9.54;machine=Helmut's%20Air");
  assert.equal(clientHeader({ appVersion: "1", machine: "" }), "flavor=electron;app=1;backend=unknown;machine=unknown");
});

test("only requests to the workspace origin carry the header, built from the current identity", () => {
  let filter; let handler; let backend;
  attachClientHeader({ webRequest: { onBeforeSendHeaders: (f, h) => { filter = f; handler = h; } } }, "https://mini.ts.net:8737",
    () => ({ appVersion: "2", backendVersion: backend, machine: "Air" }));
  assert.deepEqual(filter, { urls: ["https://mini.ts.net:8737/*"] });
  let sent;
  handler({ requestHeaders: { Accept: "x" } }, (result) => { sent = result.requestHeaders; });
  assert.equal(sent.Accept, "x");
  assert.equal(sent[CLIENT_HEADER], "flavor=electron;app=2;backend=unknown;machine=Air");
  backend = "0.9.54";
  handler({ requestHeaders: {} }, (result) => { sent = result.requestHeaders; });
  assert.match(sent[CLIENT_HEADER], /backend=0\.9\.54/);
});
