import assert from "node:assert/strict";
import { test } from "node:test";
import { explainOwnedBrowser, nativeFrames, ownedBrowserChildren } from "./reopen-processes.mjs";

test("ownership follows descendants regardless of ps ordering and excludes unrelated browser/profile owners", () => {
  const table = `30 20 /private/profile/com.apple.WebKit.Networking.Development
20 10 /private/owned/Playwright.app/Contents/MacOS/Playwright
10 1 /node
40 1 /private/unrelated/Playwright.app/Contents/MacOS/Playwright
50 40 /private/unrelated/com.apple.WebKit.WebContent.Development
60 10 /bin/ps`;
  assert.deepEqual(ownedBrowserChildren(table, 10), [
    { pid: 30, kind: "Networking" },
    { pid: 20, kind: "browser" },
  ]);
});

test("native projection excludes headers, image paths, request arguments and unrelated frames", () => {
  const sample = `Process: secret owner token=secret
Call graph:
    12 WebKit::NetworkProcess::processDidReceiveMessage (in WebKit) [0x123]
    8 CFRunLoopRunSpecific (in CoreFoundation)
    7 WebKit::/private/private-profile/token=secret
    5 secret_function
Total number in stack
Binary Images:
/private/private-profile`;
  assert.deepEqual(nativeFrames(sample), ["webkit", "core-run-loop"]);
  assert.deepEqual(nativeFrames("no call graph token=secret"), []);
});

test("failure sampler is bounded and only samples children of the current driver", async () => {
  const commands = [];
  const notes = [];
  const run = async (file, args, options) => {
    commands.push({ file, args, options });
    if (file === "/bin/ps")
      return {
        stdout: `11 ${process.pid} /owned/Playwright.app/Contents/MacOS/Playwright\n12 1 /other/Playwright.app/Contents/MacOS/Playwright`,
      };
    return { stdout: "Call graph:\n  1 mach_msg_trap (in libsystem_kernel)\nBinary Images:\n/private/secret" };
  };
  await explainOwnedBrowser((note) => notes.push(note), run, "darwin");
  assert.equal(commands.length, 2);
  assert.deepEqual(commands[1].args, ["11", "1", "1", "-file", "/dev/stdout"]);
  assert.equal(commands[0].options.timeout, 1000);
  assert.equal(commands[1].options.timeout, 2000);
  assert.ok(notes.every((note) => !note.includes("secret") && !note.includes("/owned") && !note.includes("/other")));
  await explainOwnedBrowser((note) => notes.push(note), run, "linux");
  assert.equal(commands.length, 2);
});

test("missing process evidence never replaces the original failure", async () => {
  const notes = [];
  await explainOwnedBrowser(
    (note) => notes.push(note),
    async () => {
      throw new Error("secret");
    },
    "darwin",
  );
  assert.deepEqual(notes, ["owned browser process evidence unavailable"]);
});

test("native projection never emits injected URLs, paths, credentials or oversized sample lines", () => {
  const sample = `Call graph:
1 WebKit::request https://example.invalid/?secret=value
2 IPC::/opt/customer/private-file credential=secret
3 CFNetwork Bearer confidential-value
4 CookieStorage password=secret
5 WTF::${"x".repeat(2000)}
Binary Images:
/opt/private`;
  assert.deepEqual(nativeFrames(sample), ["webkit", "ipc", "network", "cookies"]);
  assert.ok(nativeFrames(sample).every((category) => /^[a-z-]+$/.test(category)));
});
