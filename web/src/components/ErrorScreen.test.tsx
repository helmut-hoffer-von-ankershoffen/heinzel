import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Providers, queryClient } from "@/App";
import { retryDelay } from "@/splash/machine";
import { config, shell } from "@/test/fixtures";
import { expectAccessible, mockApi, renderAt } from "@/test/render";
import { ErrorBoundary, ErrorScreen } from "./ErrorScreen";

const quiet = {
  ...shell,
  "/api/system/config/show": {
    ...config,
    settings: [
      ...config.settings,
      {
        section: "hz",
        key: "playful",
        value: false,
        default: true,
        source: "user",
        env: "HZ_PLAYFUL",
        description: "x",
      },
    ],
  },
};

function Boom(): never {
  throw new Error("kaputt");
}

let broken = true;
function Maybe() {
  return broken ? <Boom /> : <p>fine</p>;
}

describe("the friendly error screen", () => {
  it("shows the scene, a warm headline, folded detail and a working Try again", async () => {
    mockApi(shell);
    const retry = vi.fn();
    const { container } = render(
      <Providers client={queryClient()}>
        <ErrorScreen title="Oh no" message="Soon fixed." detail="Error: kaputt" onRetry={retry} />
      </Providers>,
    );
    await waitFor(() => expect(screen.getByTestId("error-scene")).toBeInTheDocument());
    expect(container.querySelector('svg[data-state="blocked"]')).not.toBeNull();
    expect(screen.getByRole("heading", { name: "Oh no" })).toBeInTheDocument();
    expect(screen.getByText("Error: kaputt")).not.toBeVisible();
    await userEvent.click(screen.getByText("Show details"));
    expect(screen.getByText("Error: kaputt")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(retry).toHaveBeenCalledOnce();
    await expectAccessible(container);
  });

  it("hides the art but keeps the words when playful is off", async () => {
    mockApi(quiet);
    render(
      <Providers client={queryClient()}>
        <ErrorScreen title="Oh no" message="Soon fixed." />
      </Providers>,
    );
    await new Promise((done) => setTimeout(done, 50));
    expect(screen.queryByTestId("error-scene")).toBeNull();
    expect(screen.getByText("Soon fixed.")).toBeInTheDocument();
  });

  it("animates only without reduced motion", async () => {
    mockApi(shell);
    const { container } = render(
      <Providers client={queryClient()}>
        <ErrorScreen title="Oh no" />
      </Providers>,
    );
    await waitFor(() => expect(screen.getByTestId("error-scene")).toBeInTheDocument());
    const animated = [...container.querySelectorAll("[class*='animate-']")];
    expect(animated.length).toBeGreaterThan(0);
    for (const el of animated) expect(el.getAttribute("class")).toContain("motion-safe:animate-");
  });

  it("catches a render error in the boundary and recovers on Try again", async () => {
    mockApi(shell);
    vi.spyOn(console, "error").mockImplementation(() => {});
    broken = true;
    render(
      <Providers client={queryClient()}>
        <ErrorBoundary>
          <Maybe />
        </ErrorBoundary>
      </Providers>,
    );
    expect(screen.getByRole("heading", { name: "Oh no, we dropped something" })).toBeInTheDocument();
    broken = false;
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(screen.getByText("fine")).toBeInTheDocument();
  });

  it("is the 404 page too, with the way home as a link", async () => {
    mockApi(shell);
    const { container } = renderAt("/nowhere");
    expect(await screen.findByRole("link", { name: "Go home" })).toBeInTheDocument();
    await expectAccessible(container.querySelector("main") ?? container);
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("explains a failed call and retries it", async () => {
    const api = mockApi({ ...shell, "/api/team/show": { status: 422, body: { error: "boom" } } });
    renderAt("/team");
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't load this: boom");
    const before = api.calls.length;
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(api.calls.length).toBeGreaterThan(before));
  });
});

describe("the wait between questions to hz", () => {
  it("starts at a second, doubles, and stops at the cap", () => {
    expect([1, 2, 3, 4, 5, 6, 7, 20].map(retryDelay)).toEqual([1000, 1000, 1000, 2000, 4000, 8000, 8000, 8000]);
  });
});
