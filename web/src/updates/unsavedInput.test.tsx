// Unsent input that nothing saves holds an update reload in front and in the background (heinzel-management-uke3):
// real editors that are no chat composer, a plain field, a contenteditable and a registered custom editor.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it } from "vitest";
import { AgentProposal } from "@/chat/AgentProposal";
import { StartHelper } from "@/heinzel/StartHelper";
import { config, shell } from "@/test/fixtures";
import { mockApi } from "@/test/render";
import { reloadBlocker } from "./staleBuild";
import { registerUnsavedEditor, unsavedInput } from "./work";

const sessionId = "hz-01ARZ3NDEKTSV4RRFFQ69G5FAV";

afterEach(() => {
  document.body.innerHTML = "";
});

/** Typed text holds the reload whether the window is in front or not; cleared, it no longer does. */
async function holdsUntilCleared(field: HTMLElement) {
  expect(reloadBlocker(true)).toBeNull();
  await userEvent.type(field, "Half a thought");
  expect(reloadBlocker(true)).toBe("input");
  expect(reloadBlocker(false)).toBe("input");
  await userEvent.clear(field);
  expect(reloadBlocker(false)).toBeNull();
}

describe("unsent input nothing saves", () => {
  it("holds in the scheduled agent proposal's instructions", async () => {
    mockApi({ "/api/heinzel/latest-scheduled-agent-proposal": () => null });
    render(<AgentProposal sessionId={sessionId} />);
    await holdsUntilCleared(await screen.findByLabelText("Instructions"));
  });

  it("holds in the start helper's first message", async () => {
    mockApi({
      ...shell,
      "/api/system/config/show": {
        ...config,
        settings: [
          ...config.settings,
          { section: "hz", key: "features", value: ["connected_frontdoor"], description: "", env: "HZ_FEATURES" },
        ],
      },
      "/api/heinzel/sessions": { sessions: [], next: [] },
    });
    render(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter>
          <StartHelper />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await userEvent.click(await screen.findByText("Start a helper from your first message"));
    await holdsUntilCleared(screen.getByLabelText("First message"));
  });

  it("does not hold for a chat composer, whose draft is saved and restored", async () => {
    render(<textarea aria-label="Message" data-hz-restorable="" />);
    await userEvent.type(screen.getByLabelText("Message"), "Half a thought");
    expect(unsavedInput()).toBe(false);
  });

  it("counts typed text only, not a field filled in by the app or a switch", async () => {
    render(
      <>
        <input aria-label="Name" defaultValue="Filled in" />
        <input aria-label="On" type="checkbox" />
      </>,
    );
    expect(unsavedInput()).toBe(false);
    await userEvent.click(screen.getByLabelText("On"));
    expect(unsavedInput()).toBe(false);
  });

  it("holds for a contenteditable and a registered editor", async () => {
    render(<div data-testid="notes" contentEditable suppressContentEditableWarning />);
    await userEvent.type(screen.getByTestId("notes"), "Notes");
    expect(unsavedInput()).toBe(true);
    document.body.innerHTML = "";
    let dirty = true;
    const release = registerUnsavedEditor(() => dirty);
    try {
      expect(reloadBlocker(false)).toBe("input");
      dirty = false;
      expect(reloadBlocker(false)).toBeNull();
    } finally {
      release();
    }
  });
});
