import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { Providers, queryClient } from "@/app/providers";
import { mockApi } from "@/test/render";
import { BookmarksStrip } from "./BookmarksStrip";

afterEach(() => {
  Reflect.deleteProperty(window, "heinzelDesktop");
});

it("lists the top-level bookmarks as buttons that open in the browser's new tab", async () => {
  const browser = vi.fn(async () => ({ status: "ready" }));
  Object.defineProperty(window, "heinzelDesktop", { value: { browser }, configurable: true });
  mockApi({
    "/api/system/bookmark/list": {
      bookmarks: [
        { id: "b-1", kind: "bookmark", title: "Docs", url: "https://docs.example/", origin: null, parent: null },
        { id: "b-2", kind: "folder", title: "Work", url: null, origin: null, parent: null },
      ],
      favicons: {},
    },
  });
  render(
    <Providers client={queryClient()}>
      <BookmarksStrip />
    </Providers>,
  );
  const docs = await screen.findByRole("button", { name: "Docs" });
  expect(screen.queryByRole("button", { name: "Work" })).toBeNull();
  await userEvent.setup().click(docs);
  await waitFor(() => expect(browser).toHaveBeenCalledWith({ action: "new-tab", url: "https://docs.example/" }));
});
