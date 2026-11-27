import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import { createServer } from "node:http";
import { createConnection, createServer as createControlServer } from "node:net";
import { chmod, mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { delimiter, dirname, join } from "node:path";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";

const packageRoot = dirname(dirname(fileURLToPath(import.meta.url)));
const packagedExecutable = process.env.HEINZEL_ELECTRON_EXECUTABLE;
if (packagedExecutable && !(process.env.CI === "true" && process.env.HZ_DISPOSABLE_NATIVE_CI === "1"))
  throw new Error("Packaged native smoke requires an explicitly disposable CI environment; profile isolation is unverified.");
const OUTPUT_LIMIT = 8192;
const token = randomBytes(24).toString("base64url");
const controlToken = randomBytes(24).toString("base64url");
// Keep an executable assertion beside the capture helper so a future refactor cannot silently make it unbounded.
const captureCheck = boundedCapture(4);
captureCheck.append(Buffer.from("012345"));
assert.equal(captureCheck.read().byteLength, 4);
assert.equal(captureCheck.read().truncated, true);
assert.equal(captureCheck.read().text, "2345");
assert.equal(
  escapeTerminalControls("safe\n\u001b[31mred\u001b[0m\r\u0007\u009b"),
  "safe\n\\x1b[31mred\\x1b[0m\\x0d\\x07\\x9b",
);
let resolveRequest;
const request = new Promise((resolve) => {
  resolveRequest = resolve;
});
const server = createServer((incoming, outgoing) => {
  if (incoming.method === "GET" && incoming.url === "/") {
    outgoing.writeHead(200, { "content-type": "text/html", "content-security-policy": "default-src 'self'; script-src 'self'" });
    outgoing.end('<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Native UI fixture</title></head><body data-testid="app-shell"><p>Isolated native fixture</p><script src="/native-fixture.js"></script></body></html>');
    return;
  }
  if (incoming.method === "GET" && incoming.url === "/native-fixture.js") {
    outgoing.writeHead(200, { "content-type": "text/javascript" });
    outgoing.end('const credential = new URLSearchParams(location.hash.slice(1)).get("token"); sessionStorage.setItem("hz.token", credential); history.replaceState(null, "", "/"); window.heinzelDesktop.lifecycle().then(() => fetch("/native-ready", { headers: { Authorization: `Bearer ${credential}` } }));');
    return;
  }
  const accepted = incoming.method === "GET" && incoming.url === "/api/lifecycle";
  const ready = incoming.method === "GET" && incoming.url === "/native-ready";
  const authenticated = incoming.headers.authorization === `Bearer ${token}`;
  outgoing.writeHead((accepted || ready) && authenticated ? 200 : 403, { "content-type": "application/json" });
  outgoing.end(JSON.stringify({ instance: "native-smoke", version: "0.0.0", restartable: true }));
  if (ready) resolveRequest(authenticated);
});
let controlSocket;
const controlSockets = new Set();
let resolveFixtureStopped;
const fixtureStopped = new Promise((resolve) => { resolveFixtureStopped = resolve; });
const controlServer = createControlServer((socket) => {
  controlSockets.add(socket);
  socket.once("close", () => controlSockets.delete(socket));
  let input = "";
  socket.on("data", (chunk) => {
    input += chunk.toString("utf8");
    if (input.length > 256) {
      socket.destroy();
      return;
    }
    const newline = input.indexOf("\n");
    if (newline < 0) return;
    if (input.slice(0, newline) !== `HELLO ${controlToken}`) {
      socket.destroy();
      return;
    }
    input = input.slice(newline + 1);
    controlSocket = socket;
    socket.write(`READY ${controlToken}\n`);
    socket.removeAllListeners("data");
    socket.on("data", (command) => {
      if (command.toString("utf8") === `STOP ${controlToken}\n`) socket.end();
    });
  });
  socket.once("close", resolveFixtureStopped);
});
let fixtureDir;
let child;
const stdout = boundedCapture(OUTPUT_LIMIT);
const stderr = boundedCapture(OUTPUT_LIMIT);
let exited;
let outputClosed;
let timer;
let outcome;
let shutdownError;

try {
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  await new Promise((resolve, reject) => {
    controlServer.once("error", reject);
    controlServer.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  assert.ok(address && typeof address === "object");
  const controlAddress = controlServer.address();
  assert.ok(controlAddress && typeof controlAddress === "object");
  fixtureDir = await mkdtemp(join(tmpdir(), "heinzel-electron-native-"));
  await Promise.all(["electron-profile", "electron-session", "electron-logs"].map((name) => mkdir(join(fixtureDir, name))));
  const fixtureCli = join(fixtureDir, "hz");
  const url = `http://127.0.0.1:${address.port}/#token=${token}`;
  await writeFile(fixtureCli, `#!/usr/bin/env node\nimport { createConnection } from "node:net";\nconst expected = ["ui", "attach", "--bridge-token-stdin"];\nif (JSON.stringify(process.argv.slice(2)) !== JSON.stringify(expected)) process.exit(64);\nconst control = createConnection({ host: "127.0.0.1", port: Number(process.env.HZ_TEST_CONTROL_PORT) });\ncontrol.once("connect", () => { control.write("HELLO " + process.env.HZ_TEST_CONTROL_TOKEN + "\\n"); });\nlet input = "";\ncontrol.on("data", (chunk) => { input += chunk.toString("utf8"); if (input.includes("READY " + process.env.HZ_TEST_CONTROL_TOKEN + "\\n")) { process.stdout.write(process.env.HZ_TEST_URL + "\\n", () => process.exit(0)); input = ""; } });\ncontrol.on("close", () => process.exit(0));\nprocess.on("SIGINT", () => { control.end(); process.exit(0); });\nsetInterval(() => {}, 1000);\n`, { mode: 0o700 });
  await chmod(fixtureCli, 0o700);
  const receiptDir = join(fixtureDir, ".config", "heinzel");
  await mkdir(receiptDir, { recursive: true });
  await writeFile(join(receiptDir, "desktop-use.lock"), "", { mode: 0o600 });
  await writeFile(join(receiptDir, "desktop-owned-cli.json"), JSON.stringify({ version: 1, executable: fixtureCli }), { mode: 0o600 });
  child = spawn(
    packagedExecutable ?? createRequire(import.meta.url)("electron"),
    packagedExecutable ? [] : [join(packageRoot, "e2e", "real-backend-bootstrap.mjs")],
    {
      cwd: packageRoot,
      env: {
        PATH: `${fixtureDir}${delimiter}${process.env.PATH || ""}`,
        HOME: fixtureDir,
        TMPDIR: fixtureDir,
        XDG_CONFIG_HOME: fixtureDir,
        HZ_NATIVE_TEST_ROOT: fixtureDir,
        ...(process.env.DISPLAY ? { DISPLAY: process.env.DISPLAY } : {}),
        ...(process.env.XAUTHORITY ? { XAUTHORITY: process.env.XAUTHORITY } : {}),
        HZ_TEST_URL: url,
        HZ_TEST_CONTROL_PORT: String(controlAddress.port),
        HZ_TEST_CONTROL_TOKEN: controlToken,
      },
      stdio: ["ignore", "pipe", "pipe"],
    },
  );
  child.stdout.on("data", stdout.append);
  child.stderr.on("data", stderr.append);
  exited = new Promise((resolve) => child.once("exit", (code, signal) => resolve({ code, signal })));
  const failed = new Promise((resolve) => {
    child.once("error", (error) => resolve({ kind: "error", message: error.message }));
  });
  outputClosed = new Promise((resolve) => child.once("close", resolve));
  outcome = await Promise.race([
    request.then((authenticated) => ({ kind: "request", authenticated })),
    exited.then((result) => ({ kind: "exit", ...result })),
    failed,
    new Promise((resolve) => {
      timer = setTimeout(() => resolve({ kind: "timeout" }), 15_000);
    }),
  ]);
} finally {
  clearTimeout(timer);
  if (child?.pid !== undefined) {
    if (child.exitCode === null && child.signalCode === null) child.kill("SIGTERM");
    let stopped = await settlesWithin(exited, 5_000);
    if (!stopped) {
      child.kill("SIGKILL");
      stopped = await settlesWithin(exited, 2_000);
    }
    if (!stopped) {
      child.unref();
      shutdownError = "Electron process did not exit after SIGKILL within 2 seconds.";
    }
  }
  // Electron helpers can inherit its pipes. Drain output separately from direct process exit.
  if (child && !(await settlesWithin(outputClosed, 500))) {
    child.stdout.destroy();
    child.stderr.destroy();
  }
  if (controlSocket && !controlSocket.destroyed) {
    controlSocket.write(`STOP ${controlToken}\n`);
    await Promise.race([
      fixtureStopped,
      new Promise((resolve) => setTimeout(resolve, 2000)),
    ]);
  }
  for (const socket of controlSockets) socket.destroy();
  if (controlServer.listening) await new Promise((resolve) => controlServer.close(resolve));
  if (server.listening) await new Promise((resolve) => server.close(resolve));
  if (fixtureDir && !shutdownError) await rm(fixtureDir, { recursive: true, force: true });
}

const diagnostics = childDiagnostics(stdout.read(), stderr.read());
if (shutdownError)
  throw new Error(`${shutdownError} Startup result: ${JSON.stringify(outcome)}. Fixture retained at ${fixtureDir}.${diagnostics}`);
assert.equal(
  outcome.kind,
  "request",
  `Electron did not render the local UI fixture: ${JSON.stringify(outcome)}${diagnostics}`,
);
assert.equal(
  outcome.authenticated,
  true,
  `Electron did not preserve the authenticated loopback connection.${diagnostics}`,
);
console.log("Electron 45 rendered the native UI fixture and made its authenticated lifecycle request.");

async function settlesWithin(promise, timeoutMs) {
  let deadline;
  try {
    return await Promise.race([
      promise.then(() => true),
      new Promise((resolve) => { deadline = setTimeout(() => resolve(false), timeoutMs); }),
    ]);
  } finally {
    clearTimeout(deadline);
  }
}

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
    const text = result.text.replaceAll(token, "[redacted]").replaceAll(controlToken, "[redacted]");
    return `--- Electron ${name} ---\n${note}\n${escapeTerminalControls(text)}`;
  };
  return [
    "",
    `Electron child output (bounded to ${OUTPUT_LIMIT} bytes per stream):`,
    output("stdout", stdoutResult),
    output("stderr", stderrResult),
  ].join("\n");
}

function escapeTerminalControls(value) {
  return value.replace(/[\u0000-\u001f\u007f-\u009f]/g, (character) => {
    if (character === "\n") return "\n";
    return `\\x${character.charCodeAt(0).toString(16).padStart(2, "0")}`;
  });
}
