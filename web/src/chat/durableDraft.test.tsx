import { act, renderHook } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { reloadHeld } from "@/updates/work";
import { useDurableDraft } from "./durableDraft";

afterEach(() => sessionStorage.clear());

it("holds the reload after an automatic update while the durable composer has unsent work", () => {
  const hook = renderHook(() => useDurableDraft("session-1"));
  expect(reloadHeld()).toBe(false);
  act(() => {
    hook.result.current.update({ text: "Not sent yet" });
  });
  expect(reloadHeld()).toBe(true);
  act(() => {
    hook.result.current.update({ text: "" });
  });
  expect(reloadHeld()).toBe(false); // an empty composer releases it
  hook.unmount();
  expect(reloadHeld()).toBe(false);
});
