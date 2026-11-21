// Test-only stand-in for the owned web service in an isolated profile, for the opt-in real-backend and browser journeys.
//
// The app runs `hz ui attach --bridge-token-stdin`. The real attach would install a launchd or systemd job, and that
// job's label is shared with the person's own service, so an isolated profile must never do it. This fixture takes the
// supervised service's place: the first attach starts one detached `hz ui serve --port 0 --no-browser` of the real CLI,
// which is not the app's child, with the bridge token the app gave on stdin. It records the pid and link in the
// isolated root, prints the link and exits 0, as the real attach does. Later attaches print the same link. Every
// other command goes to the real CLI unchanged. The harness, not the app, stops the service it started (`stopService`).
import { spawn, spawnSync } from "node:child_process";
import { closeSync, openSync, readFileSync, writeFileSync } from "node:fs";
import { writeFile } from "node:fs/promises";
import { join } from "node:path";

export const ATTACH = ["ui", "attach", "--bridge-token-stdin"];
const BRIDGE_ENV = "HEINZEL_UI_BROWSER_BRIDGE";
const STARTUP_MS = 60_000;

/** Put this fixture first on the app's PATH as `hz`, in front of the real CLI at `realCli`. */
export async function installIsolatedService(bin, realCli) {
  const self = new URL(import.meta.url).pathname;
  await writeFile(join(bin, "hz"),
    `#!${process.execPath}\nprocess.env.HZ_FIXTURE_REAL_CLI = ${JSON.stringify(realCli)};\nimport(${JSON.stringify(self)}).catch(() => { process.exitCode = 1; });\n`,
    { mode: 0o700 });
}

/** The pid of the service this fixture started in `root`, or null. */
export function servicePid(root) {
  try {
    const pid = Number(readFileSync(join(root, "service-pid"), "utf8"));
    return Number.isSafeInteger(pid) && pid > 1 ? pid : null;
  } catch { return null; }
}

/** Stop only the service this fixture started (SIGINT, then SIGKILL after `ms`); true when it is gone. */
export async function stopService(root, ms = 8000) {
  const pid = servicePid(root);
  if (pid === null) return true;
  const alive = () => { try { process.kill(pid, 0); return true; } catch (error) { return error.code !== "ESRCH"; } };
  if (!alive()) return true;
  try { process.kill(pid, "SIGINT"); } catch { /* gone */ }
  const deadline = Date.now() + ms;
  while (alive() && Date.now() < deadline) await new Promise((done) => setTimeout(done, 100));
  if (alive()) { try { process.kill(pid, "SIGKILL"); } catch { /* gone */ } }
  await new Promise((done) => setTimeout(done, 200));
  return !alive();
}

function readStdin() {
  try { return readFileSync(0, "utf8").split("\n")[0].trim(); } catch { return ""; }
}

async function attach(realCli, root) {
  const token = readStdin();
  const pid = servicePid(root);
  if (pid !== null) {
    try {
      process.kill(pid, 0);
      process.stdout.write(`${readFileSync(join(root, "service-url"), "utf8")}\n`);
      return 0;
    } catch { /* gone: start a new one */ }
  }
  const out = join(root, "service.out");
  const output = openSync(out, "w", 0o600);
  const errors = openSync(join(root, "service.err"), "a", 0o600);
  const service = spawn(realCli, ["ui", "serve", "--port", "0", "--no-browser"], {
    detached: true,
    stdio: ["ignore", output, errors],
    env: { ...process.env, ...(token ? { [BRIDGE_ENV]: token } : {}) },
  });
  closeSync(output);
  closeSync(errors);
  service.unref();
  writeFileSync(join(root, "service-pid"), String(service.pid), { mode: 0o600 });
  const deadline = Date.now() + STARTUP_MS;
  while (Date.now() < deadline) {
    const line = readFileSync(out, "utf8").split("\n");
    if (line.length > 1 && line[0].startsWith("http://127.0.0.1:")) {
      writeFileSync(join(root, "service-url"), line[0], { mode: 0o600 });
      process.stdout.write(`${line[0]}\n`);
      return 0;
    }
    if (service.exitCode !== null) break;
    await new Promise((done) => setTimeout(done, 100));
  }
  process.stderr.write("The isolated service did not start.\n");
  return 1;
}

const realCli = process.env.HZ_FIXTURE_REAL_CLI;
if (realCli) {
  const args = process.argv.slice(2);
  if (JSON.stringify(args) === JSON.stringify(ATTACH)) {
    process.exitCode = await attach(realCli, process.env.HZ_NATIVE_TEST_ROOT);
  } else {
    const result = spawnSync(realCli, args, { stdio: "inherit" });
    process.exitCode = result.status ?? 1;
  }
}
