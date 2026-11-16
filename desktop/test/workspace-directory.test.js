import assert from "node:assert/strict";
import test from "node:test";
import { createWorkspaceDirectory, parseSharedWorkspace, parseWorkspaceLink, workspaceName } from "../src/workspace-directory.js";
import { authenticatedUiUrl, isOwnedUiNavigation } from "../src/ui-navigation.js";
import { lifecycle } from "../src/connection.js";

const token = "t".repeat(32);
const target = parseWorkspaceLink(`https://studio.example/#token=${token}`);

test("shared links carry only a validated workspace address", () => {
  const server = encodeURIComponent("https://studio.example");
  assert.equal(parseSharedWorkspace(`heinzel://connect?server=${server}`), target.origin);
  assert.equal(parseSharedWorkspace(`https://heinzel.team/open/#server=${server}`), target.origin);
  for (const value of [`heinzel://connect?server=${server}&server=${server}`, `heinzel://connect?server=${server}#x`,
    `https://heinzel.team/open/#server=${server}&token=${token}`, `https://user@heinzel.team/open/#server=${server}`,
    `heinzel://connect?server=${encodeURIComponent(`https://studio.example/#token=${token}`)}`,
    `heinzel://connect?server=${encodeURIComponent("http://127.0.0.1")}`])
    assert.throws(() => parseSharedWorkspace(value));
});
function fixture(options = {}) {
  let bytes = options.bytes ?? "[]";
  let mode;
  const safeStorage = {
    isEncryptionAvailable: () => options.available !== false,
    getSelectedStorageBackend: () => options.backend ?? "keychain",
    encryptString: (text) => Buffer.from([...text].reverse().join("")),
    decryptString: (buffer) => [...buffer.toString()].reverse().join(""),
  };
  const args = { file: "/fixture/workspaces.json", safeStorage, read: () => bytes,
    write: (_file, text, settings) => { if (options.failWrite) throw new Error("unwritable"); bytes = text; mode = settings.mode; },
    rename() {}, mkdir() {}, now: () => 123 };
  return { store: createWorkspaceDirectory(args), reload: () => createWorkspaceDirectory(args), bytes: () => bytes, mode: () => mode };
}

test("remote links accept HTTPS roots and never expand the local URL parser", () => {
  assert.deepEqual(target, { origin: "https://studio.example", token, remote: true });
  assert.equal(authenticatedUiUrl(target), `https://studio.example/#token=${token}`);
  assert.equal(isOwnedUiNavigation("https://studio.example/chat", target), true);
  assert.equal(isOwnedUiNavigation("https://other.example", target), false);
  for (const value of ["http://127.0.0.1:8737", "https://user@studio.example", "https://studio.example/path",
    "https://studio.example/?token=bad", "https://studio.example/#token=short", "file:///x", "https://studio.example/\n",
    "https://studio.example/\\other", "https://studio.example/#other=bad", "https://studio.example/", "x".repeat(4097), null,
    `https:studio.example/#token=${token}`, `https://studio.example/../#token=${token}`, `https://studio.example:0/#token=${token}`])
    assert.throws(() => parseWorkspaceLink(value));
  assert.equal(parseWorkspaceLink("https://studio.example:443", { requireToken: false }).origin, target.origin);
  assert.equal(workspaceName("https://blue-macbook.private.ts.net"), "Blue MacBook");
});

test("directory persists ciphertext bound to origin and publishes no secret to the picker", () => {
  const fixtureStore = fixture();
  assert.equal(fixtureStore.store.remember(target), true);
  assert.equal(fixtureStore.mode(), 0o600);
  assert.equal(fixtureStore.bytes().includes(token), false);
  assert.deepEqual(fixtureStore.reload().connection(target.origin), target);
  assert.deepEqual(fixtureStore.store.list(), [{ origin: target.origin, name: "studio.example", lastUsed: 123, signedIn: true }]);
  const swapped = JSON.parse(fixtureStore.bytes()); swapped[0].origin = "https://other.example";
  assert.equal(fixture({ bytes: JSON.stringify(swapped) }).store.connection("https://other.example"), null);
  assert.equal(fixtureStore.store.forget(target.origin), true);
  assert.deepEqual(fixtureStore.reload().list(), []);
});

for (const options of [{ available: false }, { backend: "basic_text" }, { failWrite: true }]) {
  test(`unavailable secure persistence keeps access only in memory: ${JSON.stringify(options)}`, () => {
    const f = fixture(options);
    assert.equal(f.store.remember(target), false);
    assert.deepEqual(f.store.connection(target.origin), target);
    assert.equal(f.bytes().includes(token), false);
    assert.equal(f.reload().connection(target.origin), null);
  });
}

test("malformed saved entries and ciphertext cannot create a connection", () => {
  for (const bytes of ["not json", "{}", '[{"origin":"http://127.0.0.1","lastUsed":1}]',
    '[{"origin":"https://studio.example","lastUsed":1,"encrypted":"broken"}]']) {
    assert.equal(fixture({ bytes }).store.connection(target.origin), null);
  }
});

test("remote verification sends only the selected token and refuses redirects", async () => {
  const calls = [];
  await lifecycle(target, async (...args) => { calls.push(args); return { ok: true, json: async () => ({ instance: "fixture", version: "1" }) }; });
  assert.equal(calls[0][0], "https://studio.example/api/lifecycle");
  assert.equal(calls[0][1].headers.Authorization, `Bearer ${token}`);
  assert.equal(calls[0][1].redirect, "error");
  await assert.rejects(lifecycle(target, async () => ({ ok: false, status: 401 })), { status: 401 });
});
