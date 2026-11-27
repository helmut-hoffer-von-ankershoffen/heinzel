import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("../App/Navigation.swift", import.meta.url), "utf8");
const version = source.split("private func resolveWorkspaceVersion")[1].split("private func resolveWorkspaceName")[0];
const body = version.split('body: """')[1].split('""")')[0];
const inspection = source.split("private func inspectWorkspace")[1].split('callAsyncJavaScript("""')[1].split('""", arguments:')[0];
const run = new Function("location", "origin", "sessionStorage", "localStorage", "fetch", "clientIdentity", "AbortSignal",
  `return (async () => { ${inspection.replace("\\(body)", body)} })()`);

test("native reporting requires the owned document and a credential, and refuses redirects", async () => {
  const requests = [];
  const storage = (token) => ({ getItem: () => token });
  const fetch = async (url, options) => {
    requests.push({ url, options });
    return { ok: true, json: async () => ({ web: { version: "1" } }) };
  };
  const invoke = (document, session, device, identity = "flavor=native;app=1;backend=unknown;machine=Demo") =>
    run({ origin: document }, "https://workspace.example", storage(session), storage(device), fetch, identity,
      { timeout: () => undefined });
  await invoke("https://external.example", "fixture", null);
  await invoke("https://workspace.example", null, null);
  assert.equal(requests.length, 0);
  const nativeWire = "flavor=native;app=0.9.54;backend=unknown;machine=Demo%27s%20Air%20%282%29";
  await invoke("https://workspace.example", "fixture", null, nativeWire);
  assert.equal(requests[0].url, "/api/workspace-version");
  assert.equal(requests[0].options.headers.Authorization, "Bearer fixture");
  assert.equal(requests[0].options.headers["X-Heinzel-Client"], nativeWire);
  assert.equal(requests[0].options.redirect, "error");
  assert.equal(requests[0].options.mode, "same-origin");
  assert.equal(requests[0].options.credentials, "omit");
  await invoke("https://workspace.example", null, "device-fixture");
  assert.equal(requests[1].options.headers.Authorization, "Bearer device-fixture");
  await invoke("https://workspace.example", "fixture", null, "");
  assert.equal(requests[2].options.headers["X-Heinzel-Client"], undefined);
});
