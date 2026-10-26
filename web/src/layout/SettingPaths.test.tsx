import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { SchemaSettingInfo } from "@/api/client";
import { mockApi } from "@/test/render";
import { Row } from "./SettingControls";

const setting: SchemaSettingInfo = {
  section: "sample",
  key: "folder",
  value: "/tmp/example",
  default: null,
  source: "user",
  description: "Example folder",
  env: "HZ_SAMPLE__FOLDER",
  json_schema: { type: "string", format: "path" },
};

function show(
  canReveal: boolean,
  answer: object = {
    key: "sample.folder",
    path: "/tmp/example",
    supported: true,
    opened: true,
    message: "Shown example folder",
  },
) {
  const mock = mockApi({
    "/api/system/config/show": { files: [], settings: [setting], can_reveal: canReveal, file_manager: "Finder" },
    "/api/system/config/reveal-setting": answer,
  });
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}
    >
      <ul>
        <Row setting={setting} />
      </ul>
    </QueryClientProvider>,
  );
  return mock;
}

describe("Path setting reveal", () => {
  it("calls only the key-specific private operation after an explicit click", async () => {
    const { calls } = show(true);
    const button = await screen.findByRole("button", { name: "Show in Finder: sample.folder" });
    expect(calls.some((call) => call.url.includes("reveal-setting"))).toBe(false);
    await userEvent.setup().click(button);
    expect(await screen.findByRole("status")).toHaveTextContent("Shown example folder");
    const request = calls.find((call) => call.url.includes("reveal-setting"));
    expect(request?.method).toBe("POST");
    expect(new URL(request?.url ?? "http://x").searchParams.get("key")).toBe("sample.folder");
  });

  it("does not offer an OS action when the backend reports it unavailable", async () => {
    const { calls } = show(false);
    await waitFor(() => expect(calls.some((call) => call.url.includes("config/show"))).toBe(true));
    expect(screen.queryByRole("button", { name: /Show in/ })).not.toBeInTheDocument();
    expect(calls.some((call) => call.url.includes("reveal-setting"))).toBe(false);
  });

  it("reports authorization failure without claiming the folder was opened", async () => {
    show(true, { status: 403, body: { error: "Private operation refused" } });
    await userEvent.setup().click(await screen.findByRole("button", { name: /Show in Finder/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Private operation refused");
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});
