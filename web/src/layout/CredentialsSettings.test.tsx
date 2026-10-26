import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { config, shell } from "@/test/fixtures";
import { mockApi, renderAt } from "@/test/render";

describe("credential settings ownership", () => {
  it("keeps credentials off Daemon and renders them on their own Settings page", async () => {
    mockApi({
      ...shell,
      "/api/system/config/show": {
        ...config,
        settings: [
          ...config.settings,
          {
            section: "system.daemon",
            key: "listen_host",
            value: "127.0.0.1",
            default: "127.0.0.1",
            source: "default",
            env: "HZ_SYSTEM_DAEMON__LISTEN_HOST",
            description: "The address the local service listens on.",
            json_schema: { type: "string", title: "Listen address" },
          },
          {
            section: "system.credential",
            key: "store",
            value: null,
            default: null,
            source: "default",
            env: "HZ_SYSTEM_CREDENTIAL__STORE",
            description: "Where credentials are stored.",
            json_schema: { type: "string", title: "Credential store" },
          },
        ],
      },
    });
    const user = userEvent.setup();
    renderAt("/?settings=daemon");

    const dialog = await screen.findByRole("dialog", { name: "Settings" });
    const navigation = await within(dialog).findByRole("navigation", { name: "Settings" });
    expect(await within(navigation).findByRole("button", { name: "Credentials" })).toBeInTheDocument();
    expect(await within(dialog).findByText("Listen address")).toBeInTheDocument();
    expect(within(dialog).queryByText("Credential store")).not.toBeInTheDocument();

    await user.click(within(navigation).getByRole("button", { name: "Credentials" }));
    expect(await within(dialog).findByText("Credential store")).toBeInTheDocument();
    expect(within(dialog).queryByText("Listen address")).not.toBeInTheDocument();
  });
});
