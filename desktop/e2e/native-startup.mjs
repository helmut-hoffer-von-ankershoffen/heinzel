import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const packageRoot = dirname(dirname(fileURLToPath(import.meta.url)));
const electronCli = join(packageRoot, "node_modules", "electron", "cli.js");
const OUTPUT_LIMIT = 8192;
const token = "a".repeat(32);
// Keep an executable assertion beside the capture helper so a future refactor cannot silently make it unbounded.
const captureCheck = boundedCapture(4);
captureCheck.append(Buffer.from("012345"));
assert.equal(captureCheck.read().byteLength, 4);
assert.equal(captureCheck.read().truncated, true);
assert.equal(captureCheck.read().text, "2345");
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
const stdout = boundedCapture(OUTPUT_LIMIT);
const stderr = boundedCapture(OUTPUT_LIMIT);
const child = spawn(
  process.execPath,
  [electronCli, packageRoot, `--hz-url=http://127.0.0.1:${address.port}/#token=${token}`],
  { cwd: packageRoot, env: process.env, stdio: ["ignore", "pipe", "pipe"] },
);
child.stdout.on("data", stdout.append);
child.stderr.on("data", stderr.append);
const exited = new Promise((resolve) => {
  child.once("close", (code, signal) => resolve({ code, signal }));
});
let timer;
let outcome;

try {
  outcome = await Promise.race([
    request.then((authenticated) => ({ kind: "request", authenticated })),
    exited.then((result) => ({ kind: "exit", ...result })),
    new Promise((resolve) => {
      timer = setTimeout(() => resolve({ kind: "timeout" }), 15_000);
    }),
  ]);
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

const diagnostics = childDiagnostics(stdout.read(), stderr.read());
assert.equal(
  outcome.kind,
  "request",
  `Electron did not reach the local lifecycle endpoint: ${JSON.stringify(outcome)}${diagnostics}`,
);
assert.equal(
  outcome.authenticated,
  true,
  `Electron did not preserve the authenticated loopback connection.${diagnostics}`,
);
console.log("Electron 45 opened the native window and made its authenticated lifecycle request.");

function boundedCapture(limit) {
  let chunks = [];
  let byteLength = 0;
  let truncated = false;
  return {
    append(chunk) {
      const bytes = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
      if (bytes.length === 0) return;
      if (bytes.length >= limit) {
        truncated = truncated || byteLength > 0 || bytes.length > limit;
        chunks = [Buffer.from(bytes.subarray(bytes.length - limit))];
        byteLength = limit;
        return;
      }
      chunks.push(Buffer.from(bytes));
      byteLength += bytes.length;
      while (byteLength > limit) {
        const excess = byteLength - limit;
        const first = chunks[0];
        if (first.length <= excess) {
          chunks.shift();
          byteLength -= first.length;
        } else {
          chunks[0] = Buffer.from(first.subarray(excess));
          byteLength -= excess;
        }
        truncated = true;
      }
    },
    read() {
      return {
        text: Buffer.concat(chunks, byteLength).toString("utf8"),
        byteLength,
        truncated,
      };
    },
  };
}

function childDiagnostics(stdoutResult, stderrResult) {
  const output = (name, result) => {
    const note = result.truncated ? `[earlier ${name} omitted; showing last ${OUTPUT_LIMIT} bytes]` : "";
    return `--- Electron ${name} ---\n${note}\n${result.text}`;
  };
  return [
    "",
    `Electron child output (bounded to ${OUTPUT_LIMIT} bytes per stream):`,
    output("stdout", stdoutResult),
    output("stderr", stderrResult),
  ].join("\n");
}
