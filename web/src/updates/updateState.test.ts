import { expect, it } from "vitest";
import type { AutoUpdateStatus } from "./autoUpdateApi";
import type { UpdateStatus } from "./controller";
import { describe, type Inputs } from "./updateState";

const now = new Date("2026-11-07T10:40:00");
const at = (time: string) => new Date(`2026-11-07T${time}:00`).toISOString();
const status: UpdateStatus = {
  current: "0.9.27",
  latest: "0.9.27",
  installed_at: null,
  available: false,
  supported: true,
  checked_at: at("10:32"),
  last_ok_at: at("10:32"),
  check_failed: false,
  reason: null,
};
const plan: AutoUpdateStatus = {
  enabled: true,
  reason: null,
  phase: "idle",
  version: "0.9.27",
  core: null,
  plugins: {},
  seconds_left: null,
  apply_at: null,
  next_window_at: null,
  postponements: 0,
  message: null,
  holds: [],
  checked_at: at("10:32"),
  core_checked: true,
  plugins_checked: true,
};
const base: Inputs = {
  status,
  statusFailed: false,
  plan,
  planFailed: false,
  now,
  phase: null,
  message: null,
  problem: null,
};
const texts = (input: Partial<Inputs>) => describe({ ...base, ...input }).rows.map((row) => row.text);

it("is green only when a fresh check confirmed Heinzel and the plugins", () => {
  const view = describe(base);
  expect(view.tone).toBe("ok");
  expect(view.allCurrent).toBe(true);
  expect(texts({})).toEqual(["Heinzel 0.9.27 is up to date · checked 10:32"]);
});

it("stays neutral when the plugins were not confirmed", () => {
  for (const patch of [
    { plan: { ...plan, plugins_checked: false } },
    { plan: { ...plan, plugins_checked: null } },
    { plan: { ...plan, phase: "off" as const, reason: "Automatic updates are off." } },
    { plan: undefined, planFailed: true },
  ]) {
    const view = describe({ ...base, ...patch });
    expect(view.tone).toBe("off");
    expect(view.allCurrent).toBe(false);
    expect(view.rows.map((row) => row.text).join(" ")).not.toMatch(/all .*up to date/);
  }
  expect(texts({ plan: { ...plan, plugins_checked: false } })).toContain("Couldn't check plugin updates right now");
});

it("does not call a stale check current", () => {
  const view = describe({ ...base, status: { ...status, last_ok_at: at("06:00") } });
  expect(view.tone).toBe("off");
  expect(view.rows[0]?.text).toBe("Couldn't check for updates right now");
  expect(view.rows[0]?.detail).toBe("Last successful check: 06:00");
  expect(view.canRetry).toBe(true);
});

it("says a failed check could not run, with the last good one, and offers a retry", () => {
  const view = describe({ ...base, status: { ...status, check_failed: true, checked_at: at("10:39") } });
  expect(view.rows[0]).toMatchObject({
    text: "Couldn't check for updates right now",
    detail: "Last successful check: 10:32",
    tone: "off",
  });
  expect(view.canRetry).toBe(true);
  expect(JSON.stringify(view)).not.toMatch(/unavailable/i);
});

it("says there has been no successful check yet, and checks while the first answer is awaited", () => {
  const never = describe({ ...base, status: { ...status, last_ok_at: null, check_failed: true } });
  expect(never.rows[0]).toMatchObject({
    text: "Couldn't check for updates right now",
    detail: "No successful check yet.",
  });
  const first = describe({ ...base, status: { ...status, last_ok_at: null, checked_at: null } });
  expect(first.rows[0]).toMatchObject({ text: "Checking for updates…", busy: true });
  expect(describe({ ...base, status: undefined }).rows[0]?.text).toBe("Checking for updates…");
});

it("gives Heinzel and each plugin its own line when they differ", () => {
  const view = describe({
    ...base,
    status: { ...status, latest: "0.9.28", available: true },
    plan: {
      ...plan,
      plugins: { "heinzel-weather": "0.9.3" },
      holds: [
        { name: "heinzel-google", why: "pinned", version: "0.9.1" },
        { name: "heinzel-acme", why: "source", how: "git" },
      ],
    },
  });
  expect(view.rows.map((row) => row.text)).toEqual([
    "Heinzel 0.9.28 is available",
    "heinzel-weather 0.9.3 is available",
    "heinzel-google is pinned to 0.9.1; hz doesn't update it",
    "heinzel-acme is installed from the Heinzel catalog source and isn't followed for updates yet",
  ]);
  expect(view.count).toBe(2);
  expect(view.tone).toBe("warn");
});

it("keeps core current and a held plugin from showing green", () => {
  const view = describe({
    ...base,
    plan: { ...plan, holds: [{ name: "heinzel-google", why: "pinned", version: "0.9.1" }] },
  });
  expect(view.tone).toBe("off");
  expect(view.rows[0]?.tone).toBe("ok");
  expect(view.rows.map((row) => row.text)).toContain("heinzel-google is pinned to 0.9.1; hz doesn't update it");
});

it("describes folders and links, progress, and a failed update with its reason", () => {
  expect(
    texts({
      plan: {
        ...plan,
        holds: [
          { name: "a", why: "source", how: "folder" },
          { name: "b", why: "source", how: "link" },
        ],
      },
    }),
  ).toEqual(
    expect.arrayContaining([
      "a is installed from a folder and isn't followed for updates yet",
      "b is installed from a link and isn't followed for updates yet",
    ]),
  );
  expect(describe({ ...base, phase: "Installing" }).rows[0]).toMatchObject({ text: "Installing", busy: true });
  expect(describe({ ...base, plan: { ...plan, phase: "applying" } }).rows[0]?.text).toBe("Updating Heinzel…");
  const failed = describe({ ...base, problem: "Installer failed." });
  expect(failed.tone).toBe("bad");
  expect(failed.rows[0]).toMatchObject({ text: "Installer failed.", alert: true });
});

it("says a newer release waits for its acceptance, and only while it is held", () => {
  const view = describe({
    ...base,
    status: { ...status, latest: "0.9.31", available: true },
    plan: { ...plan, core_held: "0.9.31" },
  });
  expect(view.rows.map((row) => [row.id, row.tone, row.text])).toEqual([
    ["core", "warn", "Heinzel 0.9.31 is available"],
    ["core-held", "off", "Heinzel 0.9.31 isn't accepted yet, so it won't update itself; you can still update by hand"],
  ]);
  expect(texts({ plan: { ...plan, core_held: null } })).toEqual(["Heinzel 0.9.27 is up to date · checked 10:32"]);
  expect(
    texts({ plan: { ...plan, phase: "off", reason: "Automatic updates are off.", core_held: "0.9.31" } }),
  ).not.toContain("Heinzel 0.9.31 isn't accepted yet, so it won't update itself; you can still update by hand");
});
