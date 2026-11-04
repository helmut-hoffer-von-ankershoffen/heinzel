import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it } from "vitest";
import { shell } from "@/test/fixtures";
import { expectAccessible, mockApi, renderAt } from "@/test/render";

const before = { instance: "old", version: "0.7.7", restartable: true };
const after = { ...before, instance: "new" };

it("finds Web server under This computer and confirms once before verifying a replacement, keeping open work", async () => {
  let replaced = false;
  const { calls } = mockApi({
    ...shell,
    "/api/lifecycle": () => (replaced ? after : before),
    "/api/restart": { status: 202, body: { restarting: true, instance: "old" } },
    "/api/lifecycle/verify": { instance: "new", version: before.version, matches: true, mismatched: [] },
  });
  const user = userEvent.setup();
  const { container } = renderAt("/doctor?settings=web-server");
  const dialog = await screen.findByRole("dialog", { name: "Settings" });
  expect(await within(dialog).findByRole("button", { name: "Web server" })).toHaveAttribute("aria-current", "page");
  const restart = within(dialog).getByRole("button", { name: "Restart web server" });
  await user.click(restart);
  const confirmation = within(dialog).getByRole("alertdialog", { name: "Restart web server" });
  expect(confirmation).toHaveTextContent("This page reconnects for you");
  expect(calls.filter((request) => request.url.endsWith("/api/restart"))).toHaveLength(0);
  await expectAccessible(container);
  await user.click(within(confirmation).getByRole("button", { name: "Cancel" }));
  expect(within(dialog).queryByRole("alertdialog")).not.toBeInTheDocument();
  await user.click(restart);
  await user.click(within(within(dialog).getByRole("alertdialog")).getByRole("button", { name: "Restart web server" }));
  await waitFor(() =>
    expect(within(dialog).getByRole("status")).toHaveTextContent("Waiting for the replacement server"),
  );
  expect(within(dialog).queryByText("Web server restarted and reconnected.")).not.toBeInTheDocument();
  const post = calls.filter((request) => request.url.endsWith("/api/restart"));
  expect(post).toHaveLength(1);
  expect(post[0]?.method).toBe("POST");
  expect(post[0]?.headers.get("Authorization")).toBe("Bearer test-token");
  replaced = true;
  await waitFor(() =>
    expect(within(dialog).getByRole("status")).toHaveTextContent("Web server restarted and reconnected."),
  );
  expect(screen.getByRole("dialog", { name: "Settings" })).toBe(dialog);
  const verify = calls.find((request) => request.url.endsWith("/api/lifecycle/verify"));
  expect(await verify?.json()).toEqual({ instance: "new", version: before.version, settings: [] });
  expect(calls.filter((request) => request.url.endsWith("/api/restart"))).toHaveLength(1);
});

it("shows a refused installation mutation gate without claiming a restart or replaying it", async () => {
  const { calls } = mockApi({
    ...shell,
    "/api/lifecycle": before,
    "/api/restart": { status: 400, body: { error: "An installation change requires completion before restarting." } },
  });
  const user = userEvent.setup();
  renderAt("/doctor?settings=web-server");
  const dialog = await screen.findByRole("dialog", { name: "Settings" });
  await user.click(await within(dialog).findByRole("button", { name: "Restart web server" }));
  await user.click(within(within(dialog).getByRole("alertdialog")).getByRole("button", { name: "Restart web server" }));
  expect(await within(dialog).findByRole("alert")).toHaveTextContent("installation change requires completion");
  expect(within(dialog).queryByText("Web server restarted and reconnected.")).not.toBeInTheDocument();
  expect(within(dialog).getByRole("button", { name: "Check again" })).toBeVisible();
  expect(calls.filter((request) => request.url.endsWith("/api/restart"))).toHaveLength(1);
});
