import { act, render, waitFor } from "@testing-library/react";
import { expect, it } from "vitest";
import { Providers, queryClient } from "@/App";
import { theme, themeList } from "@/test/fixtures";
import { mockApi } from "@/test/render";
import { AvatarThemeProvider } from "./AvatarTheme";
import { HeinzelAvatar } from "./HeinzelAvatar";

it("updates mounted avatars when the theme changes and removes the decoration on return", async () => {
  const winter = theme({
    name: "winter",
    title: "Winter",
    heinzel: { palette: { hat: "#abcdef" }, decorations: ["snow"] },
  });
  const original = theme();
  mockApi({ "/api/system/theme/list": themeList("winter", [original, winter]) });
  const client = queryClient();
  const { container } = render(
    <Providers client={client}>
      <AvatarThemeProvider>
        <HeinzelAvatar />
      </AvatarThemeProvider>
    </Providers>,
  );
  await waitFor(() => expect(container.querySelector('[fill="#abcdef"]')).not.toBeNull());
  act(() => client.setQueryData(["system", "theme", "list"], themeList("default", [original, winter])));
  await waitFor(() => expect(container.querySelector('[fill="#abcdef"]')).toBeNull());
});
