import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { expect, it } from "vitest";
import { expectAccessible } from "@/test/render";
import { SettingsShortcut } from "./SettingsShortcut";

it("keeps the current screen and panel, names the compact icon and clears an old settings search", async () => {
  const { container } = render(
    <MemoryRouter initialEntries={["/contracts/me%3Adigest?panel=world-clock&q=old#draft"]}>
      <SettingsShortcut section="clock" label="Open clock settings" />
    </MemoryRouter>,
  );
  const link = screen.getByRole("link", { name: "Open clock settings" });
  expect(link).toHaveAttribute("title", "Open clock settings");
  expect(link).toHaveAttribute("href", "/contracts/me%3Adigest?panel=world-clock&settings=clock#draft");
  expect(link.textContent).toBe("");
  await expectAccessible(container);
});
