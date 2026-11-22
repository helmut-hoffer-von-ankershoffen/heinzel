import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it } from "vitest";
import { mockApi } from "@/test/render";
import { PluginUnpin } from "./PluginUnpin";

const preview = {
  distribution: "heinzel-acme",
  version: "1.0.0",
  done: false,
  confirmation: "abc123",
  message: "Remove the version pin for heinzel-acme 1.0.0?",
  next: [],
};

function show() {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { mutations: { retry: false } } })}>
      <PluginUnpin name="heinzel-acme" />
    </QueryClientProvider>,
  );
}

it("previews the change, then removes the pin only after the exact preview is confirmed", async () => {
  const { calls } = mockApi({
    "/api/system/plugin/unpin": (request: Request) =>
      new URL(request.url).searchParams.has("confirm") ? { ...preview, done: true, confirmation: null } : preview,
  });
  const user = userEvent.setup();
  show();
  await user.click(screen.getByRole("button", { name: "Let it update again" }));
  expect(await screen.findByText(preview.message)).toBeInTheDocument();
  expect(calls.map((call) => new URL(call.url).searchParams.has("confirm"))).toEqual([false]);
  await user.click(screen.getByRole("button", { name: "Let heinzel-acme update again" }));
  await waitFor(() => expect(calls).toHaveLength(2));
  expect(new URL(calls[1]?.url ?? "").searchParams.get("confirm")).toBe("abc123");
});

it("says so when the pin can't be changed", async () => {
  mockApi({ "/api/system/plugin/unpin": { status: 422, body: { detail: "not pinned" } } });
  const user = userEvent.setup();
  show();
  await user.click(screen.getByRole("button", { name: "Let it update again" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't change the pin");
});

it("lets the person back out of a preview", async () => {
  mockApi({ "/api/system/plugin/unpin": preview });
  const user = userEvent.setup();
  show();
  await user.click(screen.getByRole("button", { name: "Let it update again" }));
  await user.click(await screen.findByRole("button", { name: "Keep it pinned" }));
  expect(screen.getByRole("button", { name: "Let it update again" })).toBeInTheDocument();
});
