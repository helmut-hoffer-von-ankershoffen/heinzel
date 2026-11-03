import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it } from "vitest";
import { shell } from "@/test/fixtures";
import { mockApi, renderAt } from "@/test/render";

const pending = {
  restart_required: true,
  changes: [
    { distribution: "heinzel-weather", change: "installed", version: "0.4.0", themes: ["dawn"] },
    { distribution: "heinzel-old", change: "removed", version: null, themes: [] },
  ],
};

it("tells which plugins changed outside the app and offers Restart and apply, with the theme hint", async () => {
  mockApi({ ...shell, "/api/system/plugin/pending": pending });
  const user = userEvent.setup();
  renderAt("/");
  const icon = await screen.findByRole("button", { name: "Plugins changed, warning" });
  await user.click(icon);
  expect(await screen.findByText("heinzel-weather was installed")).toBeInTheDocument();
  expect(screen.getByText("heinzel-old was removed")).toBeInTheDocument();
  expect(screen.getByText(/choose it in Settings → Appearance/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Restart and apply" })).toBeInTheDocument();
});

it("shows nothing while nothing changed", async () => {
  mockApi({ ...shell, "/api/system/plugin/pending": { restart_required: false, changes: [] } });
  renderAt("/");
  await screen.findByRole("contentinfo");
  expect(screen.queryByRole("button", { name: /Plugins changed/ })).not.toBeInTheDocument();
});
