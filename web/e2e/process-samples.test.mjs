import { expect, it } from "vitest";
import { nativeFrames } from "./process-samples.mjs";

it("keeps native diagnostic symbols and excludes process metadata and binary paths", () => {
  const frames = nativeFrames(`Process: PRIVATE_NAME
Arguments: --token=PRIVATE_TOKEN
Path: /Users/private-user/Playwright
Call graph:
    100 Thread_1
    + 100 WebKit::NetworkProcess::initialize() (in WebKit) + 10 [0x1234]
    +   100 -[NSHTTPCookieStorage init] (in CFNetwork) + 20 [0x5678]
    +     100 mach_msg2_trap (in libsystem_kernel.dylib) + 30 [0x9abc]
    + 100 WebKit::secret /Users/private-user/PRIVATE_PATH
Binary Images:
0x1234 WebKit /Users/private-user/PRIVATE_BINARY
`);
  expect(frames).toEqual([
    "+ 100 WebKit::NetworkProcess::initialize()",
    "+   100 -[NSHTTPCookieStorage init]",
    "+     100 mach_msg2_trap",
  ]);
  expect(JSON.stringify(frames)).not.toContain("PRIVATE");
});
