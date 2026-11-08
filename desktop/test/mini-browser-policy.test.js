import assert from "node:assert/strict";
import test from "node:test";
import { browserBounds, browserOwner, browserUrl, hasExactKeys } from "../src/mini-browser-policy.js";

const origin = "http://127.0.0.1:8737";
test("public HTTPS URL preserves site-specific path and rejects local or credential routes", () => {
  assert.equal(browserUrl("https://example.com/path?q=hello#section", origin), "https://example.com/path?q=hello#section");
  for (const url of ["http://example.com", "file:///etc/passwd", "javascript:alert(1)", "data:text/html,a",
    "https://user:secret@example.com", "https://127.0.0.1", "https://2130706433", "https://[::1]",
    "https://localhost", "https://app.local", "https://app.internal", "https://example.com.",
    "https://example.com\n", "https://example.com/space here", null, "x".repeat(4097)])
    assert.throws(() => browserUrl(url, origin));
  assert.throws(() => browserUrl("https://workspace.example.com/", "https://workspace.example.com"));
});
test("each non-public target class is refused, by literal address and by local or tailnet name", () => {
  const classes = {
    scheme: ["file:///Users", "data:text/html,a", "javascript:alert(1)", "blob:https://example.com/id", "http://example.com", "ftp://example.com"],
    loopback: ["https://127.0.0.1", "https://127.1.2.3", "https://[::1]", "https://localhost", "https://app.localhost"],
    private: ["https://10.0.0.1", "https://172.16.0.1", "https://172.31.255.1", "https://192.168.1.10", "https://[fc00::1]"],
    linkLocal: ["https://169.254.169.254", "https://[fe80::1]"],
    tailnet: ["https://100.64.0.1", "https://100.127.255.254", "https://mini.tail1234.ts.net", "https://ts.net"],
    localName: ["https://mini.local", "https://printer.home.arpa", "https://svc.internal", "https://nas.lan",
      "https://wiki.corp", "https://portal.intranet"],
    port: ["https://example.com:8737", "https://example.com:8443"],
  };
  for (const [name, urls] of Object.entries(classes))
    for (const url of urls) assert.throws(() => browserUrl(url, origin), undefined, `${name}: ${url}`);
  assert.equal(browserUrl("https://news.ts.network/", origin), "https://news.ts.network/");
});
test("geometry requires an exact finite integer rectangle contained in the owner", () => {
  const rectangle = { x: 600, y: 100, width: 300, height: 500 };
  assert.deepEqual(browserBounds(rectangle, [1000, 700]), rectangle);
  for (const bad of [{ ...rectangle, x: -1 }, { ...rectangle, width: 900 }, { ...rectangle, y: NaN },
    { ...rectangle, x: 1.5 }, { ...rectangle, height: 0 }, { ...rectangle, extra: true }, null])
    assert.throws(() => browserBounds(bad, [1000, 700]));
});
test("only the owned authenticated UI main frame is a browser controller", () => {
  const window = { isDestroyed: () => false };
  const frame = { processId: 1, frameToken: "main", url: `${origin}/`, isDestroyed: () => false, detached: false };
  const sender = { isDestroyed: () => false, mainFrame: frame };
  const dependencies = { windows: new WeakSet([window]), fromWebContents: () => window,
    connection: () => ({ origin, token: "a".repeat(32) }) };
  assert.equal(browserOwner({ sender, senderFrame: frame }, dependencies), window);
  for (const bad of [{ ...frame, frameToken: "sub" }, { ...frame, processId: 9 },
    { ...frame, url: "https://example.com" }, { ...frame, detached: true }, null])
    assert.throws(() => browserOwner({ sender, senderFrame: bad }, dependencies));
  assert.throws(() => browserOwner({ sender, senderFrame: frame }, { ...dependencies, windows: new WeakSet() }));
});
test("exact key sets ignore order and refuse missing, extra and inherited keys", () => {
  assert.equal(hasExactKeys({ y: 1, x: 2 }, ["x", "y"]), true);
  for (const value of [{ x: 1 }, { x: 1, y: 2, z: 3 }, { x: 1, z: 2 }, Object.assign(Object.create({ y: 1 }), { x: 1 })])
    assert.equal(hasExactKeys(value, ["x", "y"]), false);
});
