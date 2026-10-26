import { act, renderHook } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { useUpdateDraft } from "./draft";
import { preserveUpdateWork } from "./work";

afterEach(() => vi.restoreAllMocks());

it("preserves unsent text and model for an explicit update and restores under StrictMode", () => {
  const first = renderHook(() => useUpdateDraft("route:/", null));
  act(() => {
    first.result.current.setMessage("Keep my unsent thought");
    first.result.current.setPicked({ model: "demo/model", effort: "high" });
  });
  expect(sessionStorage.getItem("hz.update.composer")).toBeNull();
  preserveUpdateWork();
  first.unmount();
  const next = renderHook(() => useUpdateDraft("route:/", null), { wrapper: StrictMode });
  expect(next.result.current.message).toBe("Keep my unsent thought");
  expect(next.result.current.picked).toEqual({ model: "demo/model", effort: "high" });
  expect(sessionStorage.getItem("hz.update.composer")).toBeNull();
});

it("never restores a snapshot into another route or conversation", () => {
  const first = renderHook(() => useUpdateDraft("route:/doctor", "old"));
  act(() => first.result.current.setMessage("Only this conversation"));
  preserveUpdateWork();
  first.unmount();
  const next = renderHook(() => useUpdateDraft("route:/", "other"));
  expect(next.result.current.message).toBe("");
});

it("refuses an update before a busy turn, a modal editor or unavailable storage can lose work", () => {
  const draft = renderHook(() => useUpdateDraft("route:/", null, true));
  expect(preserveUpdateWork).toThrow(/current chat turn/);
  draft.unmount();
  const editor = document.createElement("div");
  editor.setAttribute("role", "dialog");
  document.body.appendChild(editor);
  expect(preserveUpdateWork).toThrow(/terminal or editor/);
  editor.remove();
  renderHook(() => useUpdateDraft("route:/", null));
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new Error("quota");
  });
  expect(preserveUpdateWork).toThrow("quota");
});
