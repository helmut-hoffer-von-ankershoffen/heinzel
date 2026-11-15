import { afterEach, describe, expect, it, vi } from "vitest";
import type { SchemaSessionSummary } from "@/api/client";
import { defaultHeinzel, rememberedHeinzel, rememberHeinzel } from "./defaultHeinzel";

const at = "2026-11-14T09:00:00Z";
const row = (session_id: string, state: SchemaSessionSummary["state"] = "awake"): SchemaSessionSummary => ({
  session_id,
  name: session_id,
  state,
  last_seq: 1,
  updated_at: at,
  activity: "idle",
  last_run_failed: false,
  queued_runs: 0,
  runs_last_hour: 0,
  waiting_count: 0,
});

afterEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});

describe("defaultHeinzel", () => {
  // Newest first, as `hz heinzel sessions` lists them.
  const sessions = [row("newest"), row("middle"), row("first")];

  it("opens the Heinzel you talked to last while it is still listed", () => {
    expect(defaultHeinzel(sessions, "middle")).toBe("middle");
  });

  it("falls back to your first Heinzel when nothing is remembered or the remembered one is gone", () => {
    expect(defaultHeinzel(sessions, null)).toBe("first");
    expect(defaultHeinzel(sessions, "removed")).toBe("first");
  });

  it("never opens a retired Heinzel by default", () => {
    expect(defaultHeinzel([row("newest"), row("first", "retired")], "first")).toBe("newest");
    expect(defaultHeinzel([row("only", "retired")], null)).toBeNull();
  });

  it("has no default without a Heinzel", () => {
    expect(defaultHeinzel([], "anything")).toBeNull();
  });

  it("remembers the last Heinzel in this browser", () => {
    expect(rememberedHeinzel()).toBeNull();
    rememberHeinzel("middle");
    expect(rememberedHeinzel()).toBe("middle");
  });

  it("treats storage that throws as empty", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(() => rememberHeinzel("middle")).not.toThrow();
    expect(rememberedHeinzel()).toBeNull();
  });
});
