// The footer pane structure every pane test asserts (design/README.md, "Pane actions"): content, then one divider
// and one bottom row of action buttons, and no button or link anywhere else in the pane.
import { within } from "@testing-library/react";
import { expect } from "vitest";
import { paneStructureProblems } from "@/components/FooterItem";
import { expectAccessible } from "./render";

/** The pane's actions row, by role and name; null when the pane has no action. */
export function paneActions(pane: HTMLElement): HTMLElement | null {
  return within(pane).queryByRole("group", { name: "Actions" });
}

export function expectPaneStructure(pane: HTMLElement) {
  expect(paneStructureProblems(pane)).toEqual([]);
  const row = paneActions(pane);
  if (!row) return;
  // The divider is the row's own top border, so nothing can sit between the content and it.
  expect(row.className).toMatch(/\bborder-t\b/);
  for (const button of within(row).queryAllByRole("button"))
    expect(button.textContent?.trim() || button.getAttribute("aria-label")).toBeTruthy();
}

/** The structure plus axe's WCAG A/AA rules on the open pane. */
export async function expectPane(pane: HTMLElement) {
  expectPaneStructure(pane);
  await expectAccessible(pane);
}
