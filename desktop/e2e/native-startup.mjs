import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const packageRoot = dirname(dirname(fileURLToPath(import.meta.url)));
const electronCli = join(packageRoot, "node_modules", "electron", "cli.js");
const token = "a".repeat(32);
let resolveRequest;
const request = new Promise((resolve) => {
  resolveRequest = resolve;
});
const server = createServer((incoming, outgoing) => {
  const accepted = incoming.method === "GET" && incoming.url === "/lifecycle";
  const authenticated = incoming.headers.authorization === `Bearer ${token}`;
  outgoing.writeHead(accepted && authenticated ? 200 : 403, { "content-type": "application/json" });
  outgoing.end(JSON.stringify({ instance: "native-smoke", version: "0.0.0", restartable: true }));
  if (accepted) resolveRequest(authenticated);
});

await new Promise((resolve, reject) => {
  server.once("error", reject);
  server.listen(0, "127.0.0.1", resolve);
});

const address = server.address();
assert.ok(address && typeof address === "object");
const child = spawn(
  process.execPath,
  [electronCli, packageRoot, `--hz-url=http://127.0.0.1:${address.port}/#token=${token}`],
  { cwd: packageRoot, env: process.env, stdio: "ignore" },
);
const exited = new Promise((resolve) => {
  child.once("close", (code, signal) => resolve({ code, signal }));
});
let timer;

try {
  const outcome = await Promise.race([
    request.then((authenticated) => ({ kind: "request", authenticated })),
    exited.then((result) => ({ kind: "exit", ...result })),
    new Promise((resolve) => {
      timer = setTimeout(() => resolve({ kind: "timeout" }), 15_000);
    }),
  ]);
  assert.equal(
    outcome.kind,
    "request",
    `Electron did not reach the local lifecycle endpoint: ${JSON.stringify(outcome)}`,
  );
  assert.equal(outcome.authenticated, true, "Electron did not preserve the authenticated loopback connection");
  console.log("Electron 45 opened the native window and made its authenticated lifecycle request.");
} finally {
  clearTimeout(timer);
  if (child.exitCode === null && child.signalCode === null) child.kill("SIGTERM");
  const stopped = await Promise.race([
    exited.then(() => true),
    new Promise((resolve) => setTimeout(() => resolve(false), 5_000)),
  ]);
  if (!stopped) child.kill("SIGKILL");
  await exited;
  await new Promise((resolve) => server.close(resolve));
}
