// Failure-only evidence from this driver's descendants; never inspect unrelated browsers or print command lines.
import { execFile } from "node:child_process";
import { promisify } from "node:util";

const execute = promisify(execFile);

export function ownedBrowserChildren(table, owner) {
  const rows = table.split("\n").flatMap((line) => {
    const match = /^\s*(\d+)\s+(\d+)\s+(.+)$/.exec(line);
    if (!match) return [];
    const kind = match[3].includes("Playwright.app/Contents/MacOS/Playwright")
      ? "browser"
      : /com\.apple\.WebKit\.(Networking|WebContent|GPU)\.Development$/.exec(match[3])?.[1];
    return [{ pid: Number(match[1]), parent: Number(match[2]), kind }];
  });
  const owned = new Set([owner]);
  for (let changed = true; changed; ) {
    changed = false;
    for (const row of rows) {
      if (owned.has(row.parent) && !owned.has(row.pid)) {
        owned.add(row.pid);
        changed = true;
      }
    }
  }
  return rows.filter((row) => owned.has(row.pid) && row.kind).map(({ pid, kind }) => ({ pid, kind }));
}

export function nativeFrames(sample) {
  const graph = sample.split("Call graph:")[1]?.split("Total number in stack")[0]?.split("Binary Images:")[0] ?? "";
  return graph
    .split("\n")
    .filter((line) => /^\s*[+!|: ]*\d+ /.test(line))
    .filter((line) =>
      /WebKit::|WTF::|IPC::|CFNetwork|CookieStorage|mach_msg|semaphore|dispatch_|CFRunLoop|NSRunLoop|pthread_/.test(
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
    .slice(0, 80);
}

export async function explainOwnedBrowser(note, run = execute, platform = process.platform) {
  if (platform !== "darwin") return;
  try {
    const { stdout } = await run("/bin/ps", ["-axo", "pid=,ppid=,comm="], { timeout: 1000, maxBuffer: 1024 * 1024 });
    const owned = ownedBrowserChildren(stdout, process.pid);
    note(`owned browser children ${JSON.stringify(owned)}`);
    // At most three one-second samples, in parallel, only after the original navigation has already failed.
    await Promise.all(
      owned.slice(0, 3).map(async ({ pid, kind }) => {
        try {
          const { stdout: sample } = await run("/usr/bin/sample", [String(pid), "1", "1", "-file", "/dev/stdout"], {
            timeout: 2000,
            maxBuffer: 1024 * 1024,
          });
          note(`owned native sample ${kind} pid=${pid} ${JSON.stringify(nativeFrames(sample))}`);
        } catch {
          note(`owned native sample ${kind} pid=${pid} unavailable`);
        }
      }),
    );
  } catch {
    note("owned browser process evidence unavailable");
  }
}
