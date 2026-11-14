// Look and name on the Heinzel's page: the Meet look editor, a save that applies at once with Undo, and a rename
// suggested in chat taken with one press and undone the same way.
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it } from "vitest";
import { HeinzelPage } from "@/heinzel/HeinzelPage";
import { posted, renderPage, sessionId, snapshot } from "@/test/heinzelPage";
import { mockApi } from "@/test/render";
import { LookSection } from "./LookSection";

type Body = { name: string; appearance: { hat?: string }; expected_revision: number; request_id: string };

/** hz's profile operation: each save takes the next revision; a stale one is refused. */
function profileServer() {
  let current = { ...snapshot };
  const bodies: Body[] = [];
  const answer = async (request: Request) => {
    const body = (await request.json()) as Body;
    bodies.push(body);
    if (body.expected_revision !== current.profile_revision) return { status: 409, body: { error: "stale" } };
    current = {
      ...current,
      name: body.name,
      appearance: body.appearance as typeof current.appearance,
      profile_revision: current.profile_revision + 1,
    };
    return current;
  };
  return { answer, bodies, current: () => current };
}

afterEach(() => sessionStorage.clear());

describe("look and name", () => {
  it("renames at once, and Undo puts the old name back", async () => {
    const server = profileServer();
    mockApi({
      "/api/heinzel/session": () => server.current(),
      "/api/heinzel/profile": server.answer,
      "/api/heinzel/latest-profile-proposal": null,
    });
    renderPage(<HeinzelPage />);
    await screen.findByRole("region", { name: "Look and name" });
    const name = screen.getByLabelText("Name");
    const save = screen.getByRole("button", { name: "Save" });
    expect(save).toBeDisabled();
    fireEvent.change(name, { target: { value: "Fritz" } });
    fireEvent.click(save);
    expect(await screen.findByText("Saved · Fritz")).toBeInTheDocument();
    expect(server.bodies[0]).toMatchObject({ name: "Fritz", expected_revision: 1 });
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect(await screen.findByText("Back as it was")).toBeInTheDocument();
    expect(server.bodies[1]).toMatchObject({ name: "Helmi", expected_revision: 2 });
    expect(server.bodies[1]?.request_id).not.toBe(server.bodies[0]?.request_id);
    expect(name).toHaveValue("Helmi");
    expect(screen.queryByRole("button", { name: "Undo" })).not.toBeInTheDocument();
  });

  it("changes the look with the Meet look editor: a part of the figure, then its choices", async () => {
    const server = profileServer();
    mockApi({
      "/api/heinzel/session": () => server.current(),
      "/api/heinzel/profile": server.answer,
      "/api/heinzel/latest-profile-proposal": null,
    });
    renderPage(<LookSection snapshot={snapshot} />);
    fireEvent.click(screen.getByRole("radio", { name: "Change the hat" }));
    const hats = screen.getByRole("radiogroup", { name: "Hat" });
    fireEvent.click(within(hats).getAllByRole("radio")[1] as HTMLElement);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(server.bodies).toHaveLength(1));
    expect(server.bodies[0]?.appearance.hat).toBeTruthy();
    expect(server.bodies[0]?.name).toBe("Helmi");
  });

  it("says a save did not work and keeps the person's edit", async () => {
    mockApi({
      "/api/heinzel/session": snapshot,
      "/api/heinzel/profile": { status: 503, body: { error: "down" } },
      "/api/heinzel/latest-profile-proposal": null,
    });
    renderPage(<LookSection snapshot={snapshot} />);
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Otto" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Not saved. Try again.");
    expect(screen.getByLabelText("Name")).toHaveValue("Otto");
  });

  it("never saves an empty name", () => {
    mockApi({ "/api/heinzel/latest-profile-proposal": null });
    renderPage(<LookSection snapshot={snapshot} />);
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "   " } });
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("takes a rename suggested in chat with one press, with Undo", async () => {
    const server = profileServer();
    const { calls } = mockApi({
      "/api/heinzel/session": () => server.current(),
      "/api/heinzel/profile": server.answer,
      "/api/heinzel/latest-profile-proposal": {
        session_id: sessionId,
        request_id: "look-1",
        expected_revision: 1,
        name: "Mira",
        appearance: { age: "grown", build: "regular", accessories: [] },
        status: "proposed",
        preview_sha256: "a".repeat(64),
      },
    });
    renderPage(<HeinzelPage />);
    await screen.findByRole("region", { name: "Look and name" });
    expect(await screen.findByText("Suggested in chat: Mira")).toBeInTheDocument();
    expect(posted(calls, "/heinzel/profile")).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Use it" }));
    expect(await screen.findByText("Saved · Mira")).toBeInTheDocument();
    expect(server.bodies[0]).toMatchObject({ name: "Mira", expected_revision: 1 });
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    await waitFor(() => expect(server.bodies).toHaveLength(2));
    expect(server.bodies[1]).toMatchObject({ name: "Helmi" });
  });

  it("does not offer a suggestion made for an older revision, or one that changes nothing", async () => {
    mockApi({
      "/api/heinzel/session": snapshot,
      "/api/heinzel/latest-profile-proposal": {
        session_id: sessionId,
        request_id: "look-2",
        expected_revision: 0,
        name: "Mira",
        appearance: { age: "grown", build: "regular", accessories: [] },
        status: "proposed",
        preview_sha256: "a".repeat(64),
      },
    });
    renderPage(<LookSection snapshot={snapshot} />);
    await waitFor(() => expect(screen.queryByRole("button", { name: "Use it" })).not.toBeInTheDocument());
    expect(screen.queryByText(/Suggested in chat/)).not.toBeInTheDocument();
  });

  it("ignores a suggestion for another Heinzel", async () => {
    mockApi({
      "/api/heinzel/latest-profile-proposal": {
        session_id: "hz-01ARZ3NDEKTSV4RRFFQ69G5FAW",
        request_id: "look-3",
        expected_revision: 1,
        name: "Elsewhere",
        appearance: { age: "grown", build: "regular", accessories: [] },
        status: "proposed",
        preview_sha256: "a".repeat(64),
      },
    });
    renderPage(<LookSection snapshot={snapshot} />);
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(screen.queryByText(/Elsewhere/)).not.toBeInTheDocument();
  });
});

let setHosted: (next: typeof snapshot) => void = () => {};

/** The page's part: it hands LookSection each new snapshot, as the query does when the profile changes elsewhere. */
function Host() {
  const [current, setCurrent] = useState(snapshot);
  setHosted = setCurrent;
  return <LookSection snapshot={current} />;
}

describe("an edit in progress", () => {
  it("is kept when the profile changes elsewhere, says so, and its save meets the revision check", async () => {
    const bodies: Array<{ expected_revision: number }> = [];
    mockApi({
      "/api/heinzel/latest-profile-proposal": null,
      "/api/heinzel/profile": async (request: Request) => {
        bodies.push(await request.json());
        return { status: 409, body: { error: "This Heinzel's profile changed" } };
      },
    });
    renderPage(<Host />);
    const name = await screen.findByLabelText("Name");
    fireEvent.change(name, { target: { value: "My own edit" } });
    await act(async () => setHosted({ ...snapshot, name: "Mira", profile_revision: 2 }));
    expect(screen.getByLabelText("Name")).toHaveValue("My own edit");
    expect(screen.getByText("Changed elsewhere · now Mira")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Not saved: it changed elsewhere first");
    expect(bodies).toEqual([expect.objectContaining({ expected_revision: 1, name: "My own edit" })]);
    fireEvent.click(screen.getByRole("button", { name: "Discard my edit" }));
    expect(screen.getByLabelText("Name")).toHaveValue("Mira");
    expect(screen.queryByText(/Changed elsewhere/)).not.toBeInTheDocument();
  });

  it("follows a change made elsewhere when there is no edit in progress", async () => {
    mockApi({ "/api/heinzel/latest-profile-proposal": null });
    renderPage(<Host />);
    await screen.findByLabelText("Name");
    await act(async () => setHosted({ ...snapshot, name: "Mira", profile_revision: 2 }));
    expect(screen.getByLabelText("Name")).toHaveValue("Mira");
  });

  it("keeps Use it closed while there is an edit in progress", async () => {
    mockApi({
      "/api/heinzel/latest-profile-proposal": {
        session_id: sessionId,
        request_id: "look-4",
        expected_revision: 1,
        name: "Mira",
        appearance: { age: "grown", build: "regular", accessories: [] },
        status: "proposed",
        preview_sha256: "a".repeat(64),
      },
    });
    renderPage(<LookSection snapshot={snapshot} />);
    const use = await screen.findByRole("button", { name: "Use it" });
    expect(use).toBeEnabled();
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "My own edit" } });
    expect(use).toBeDisabled();
  });

  it("moves between parts with the arrow keys, one Tab stop per group", async () => {
    mockApi({ "/api/heinzel/latest-profile-proposal": null });
    renderPage(<LookSection snapshot={snapshot} />);
    const hat = screen.getByRole("radio", { name: "Change the hat" });
    expect(hat).toHaveAttribute("tabindex", "0");
    expect(screen.getByRole("radio", { name: "Change the age" })).toHaveAttribute("tabindex", "-1");
    hat.focus();
    fireEvent.keyDown(hat, { key: "ArrowDown" });
    const age = screen.getByRole("radio", { name: "Change the age" });
    expect(age).toHaveAttribute("aria-checked", "true");
    expect(age).toHaveFocus();
    fireEvent.keyDown(age, { key: "ArrowUp" });
    fireEvent.keyDown(screen.getByRole("radio", { name: "Change the hat" }), { key: "ArrowLeft" });
    expect(screen.getByRole("radio", { name: "Change the accessories" })).toHaveAttribute("aria-checked", "true");
    fireEvent.keyDown(screen.getByRole("radio", { name: "Change the accessories" }), { key: "Enter" });
    expect(screen.getByRole("radio", { name: "Change the accessories" })).toHaveAttribute("aria-checked", "true");
  });
});
