// Native stacks only: never emit sample's process arguments, environment or binary image paths.
import { execFile } from "node:child_process";
import { promisify } from "node:util";

const execute = promisify(execFile);

export function nativeFrames(sample) {
  const graph = sample.split("Call graph:")[1]?.split("Total number in stack")[0]?.split("Binary Images:")[0] ?? "";
  return graph
    .split("\n")
    .filter((line) => /^\s*[+!|: ]*\d+ /.test(line))
    .filter((line) =>
      /WebKit::|WTF::|IPC::|NSAnimation|CookieStorage|CFNetwork|CoreAnalytics|SecKeychain|mach_msg|semaphore|dispatch_|CFRunLoop|NSRunLoop|pthread_/.test(
        line,
      ),
    )
    .map((line) =>
      line
        .split(" (in ")[0]
        .replace(/\[0x[0-9a-f]+\]/gi, "")
        .trim(),
    )
    .filter((line) => !/\/(Users|home|private|var|tmp)\/|Bearer |token=|HZ_/.test(line))
    .slice(0, 160);
}

export async function sampleDiagnosticBrowser(note) {
  // The opt-in job owns a dedicated hosted runner. This must never sample a browser on a developer's machine.
  if (
    process.env.HZ_REOPEN_DIAGNOSTICS !== "1" ||
    process.env.GITHUB_ACTIONS !== "true" ||
    process.platform !== "darwin"
  )
    return;
  const { stdout } = await execute("/bin/ps", ["-axo", "pid=,comm="], { timeout: 5000 });
  const selected = stdout.split("\n").flatMap((line) => {
    const match = /^\s*(\d+)\s+(.+)$/.exec(line);
    if (!match) return [];
    const kind = match[2].includes("Playwright.app/Contents/MacOS/Playwright")
      ? "browser"
      : /com\.apple\.WebKit\.(Networking|WebContent|GPU)\.Development/.exec(match[2])?.[1];
    return kind ? [{ pid: match[1], kind }] : [];
  });
  for (const { pid, kind } of selected) {
    try {
      const { stdout: sample } = await execute("/usr/bin/sample", [pid, "1", "1", "-file", "/dev/stdout"], {
        timeout: 5000,
        maxBuffer: 4 * 1024 * 1024,
      });
      note(`native sample ${kind} pid=${pid} ${JSON.stringify(nativeFrames(sample))}`);
    } catch {
      note(`native sample ${kind} pid=${pid} unavailable`);
    }
  }
}
