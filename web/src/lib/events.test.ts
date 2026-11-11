import { expect, it, vi } from "vitest";
import { eventLine, logEvent } from "./events";

it("writes one event as a JSON line, leaves out undefined fields and redacts secrets by key", () => {
  expect(
    JSON.parse(eventLine("web.splash.probe", { outcome: "ok", reason: undefined, device_token: "abc", nested: 1 })),
  ).toEqual({ event: "web.splash.probe", outcome: "ok", device_token: "[redacted]", nested: 1 });
});

it("never throws into the code it describes", () => {
  const warn = vi.spyOn(console, "warn").mockImplementation(() => {
    throw new Error("console gone");
  });
  try {
    expect(() => logEvent("web.shell.offline", {}, "warn")).not.toThrow();
  } finally {
    warn.mockRestore();
  }
});
