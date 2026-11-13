import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { shell } from "@/test/fixtures";
import { expectAccessible, mockApi, renderAt } from "@/test/render";

type Section = ReturnType<typeof within>;
/** The Personal Heinzels section of the page. */
const page = async (): Promise<Section> => within(await screen.findByRole("region", { name: "Personal Heinzels" }));
/** Choose a Heinzel on the page and press Edit in chat: its chat, the one editor of its name and look. */
async function editInChat(name: RegExp = /^Alpha/): Promise<Section> {
  const section = await page();
  fireEvent.click(await section.findByRole("radio", { name }));
  fireEvent.click(await section.findByRole("button", { name: "Edit in chat" }));
  return within(await screen.findByRole("complementary", { name: "Chat" }));
}
const openAlpha = () => editInChat();
/** While a save is unresolved or edits are unsaved, the chat holds: it cannot be closed. */
const held = (chat: Section) => expect(chat.getByRole("button", { name: "Back to chat" })).toBeDisabled();
const swatch = (section: Section, group: string, colour: string) =>
  within(section.getByRole("radiogroup", { name: group })).getByRole("radio", { name: colour });

const first = "hz-01ARZ3NDEKTSV4RRFFQ69G5FAV";
const second = "hz-01ARZ3NDEKTSV4RRFFQ69G5FAW";
const look = { age: "grown", build: "regular", beard: null, hat: null, shirt: null, accessories: ["scarf"] };
const snapshot = (id: string, name: string) => ({
  session_id: id,
  heinzel_id: id,
  name,
  appearance: look,
  profile_revision: 1,
});
const sessions = [
  { session_id: first, name: "Alpha", state: "proposed" },
  { session_id: second, name: "Beta", state: "proposed" },
];
const checkInSchedule = {
  summary: "Five-minute check-in",
  cron: "*/5 * * * *",
  timezone: "UTC",
  status: "proposed",
  contract_version: 1,
  revision: 1,
  preview_hash: "a".repeat(64),
};

describe("personal identities", () => {
  it("previews and saves one session without replacing another's look", async () => {
    const saved: unknown[] = [];
    let stored = snapshot(first, "Alpha");
    mockApi({
      ...shell,
      "/api/heinzel/sessions": { sessions },
      "/api/heinzel/session": (request: Request) =>
        new URL(request.url).searchParams.get("session_id") === first ? stored : snapshot(second, "Beta"),
      "/api/heinzel/profile": async (request: Request) => {
        const body = await request.json();
        saved.push(body);
        stored = { ...snapshot(body.session_id, body.name), appearance: body.appearance, profile_revision: 2 };
        return stored;
      },
    });
    renderAt("/heinzel");
    const section = await openAlpha();
    const name = await section.findByLabelText("Name");
    fireEvent.change(name, { target: { value: "Named helper" } });
    fireEvent.click(swatch(section, "Hat colour", "Blue"));
    expect(section.getByRole("img", { name: /Appearance preview/ })).toBeInTheDocument();
    fireEvent.click(section.getByRole("button", { name: "Save name and look" }));
    await waitFor(() => expect(saved).toHaveLength(1));
    expect(saved[0]).toMatchObject({
      session_id: first,
      name: "Named helper",
      expected_revision: 1,
      appearance: { hat: "blue", accessories: ["scarf"] },
    });
    await waitFor(() => expect(section.getByRole("button", { name: "Back to chat" })).toBeEnabled());
    const beta = await editInChat(/^Beta/);
    await waitFor(() => expect(beta.getByLabelText("Name")).toHaveValue("Beta"));
    expect(swatch(beta, "Hat colour", "Follow theme")).toBeChecked();
    const alpha = await editInChat(/^Named helper|^Alpha/);
    await waitFor(() => expect(alpha.getByLabelText("Name")).toHaveValue("Named helper"));
    expect(swatch(alpha, "Hat colour", "Blue")).toBeChecked();
  });

  it("lets a person choose and save the closed-set accessories shown in the preview", async () => {
    const saved: Record<string, unknown>[] = [];
    mockApi({
      ...shell,
      "/api/heinzel/sessions": { sessions },
      "/api/heinzel/session": snapshot(first, "Alpha"),
      "/api/heinzel/profile": async (request: Request) => {
        const profile = (await request.json()) as Record<string, unknown>;
        saved.push(profile);
        return {
          ...snapshot(first, "Alpha"),
          appearance: profile.appearance,
          profile_revision: 2,
        };
      },
    });
    renderAt("/heinzel");
    const section = await openAlpha();
    const scarf = await section.findByLabelText("Scarf");
    expect(scarf).toBeChecked();
    fireEvent.click(scarf);
    fireEvent.click(section.getByLabelText("Umbrella"));
    fireEvent.click(section.getByLabelText("Sunglasses"));
    expect(section.getByRole("img", { name: /with an umbrella, wearing sunglasses/ })).toBeInTheDocument();
    fireEvent.click(section.getByRole("button", { name: "Save name and look" }));
    await waitFor(() => expect(saved).toHaveLength(1));
    expect(saved[0]?.appearance).toMatchObject({ accessories: ["umbrella", "sunglasses"] });
  });

  it("restores an unsaved name and look after reload without leaking them to another Heinzel", async () => {
    const firstDraft = `hz.personal-heinzel.profile-draft.${first}`;
    const secondDraft = `hz.personal-heinzel.profile-draft.${second}`;
    sessionStorage.removeItem(firstDraft);
    sessionStorage.removeItem(secondDraft);
    mockApi({
      ...shell,
      "/api/heinzel/sessions": { sessions },
      "/api/heinzel/session": (request: Request) => {
        const id = new URL(request.url).searchParams.get("session_id");
        return snapshot(id === second ? second : first, id === second ? "Beta" : "Alpha");
      },
    });
    const firstView = renderAt("/heinzel");
    let section = await openAlpha();
    const name = await section.findByLabelText("Name");
    fireEvent.change(name, { target: { value: "Styled Alpha" } });
    fireEvent.click(section.getByLabelText("Umbrella"));
    await waitFor(() => expect(sessionStorage.getItem(firstDraft)).not.toBeNull());
    firstView.unmount();

    const secondView = renderAt("/heinzel");
    section = await openAlpha();
    await waitFor(() => expect(section.getByLabelText("Name")).toHaveValue("Styled Alpha"));
    expect(section.getByLabelText("Umbrella")).toBeChecked();
    held(section);
    secondView.unmount();
    renderAt("/heinzel");
    section = await editInChat(/^Beta/);
    await waitFor(() => expect(section.getByLabelText("Name")).toHaveValue("Beta"));
    expect(section.getByLabelText("Umbrella")).not.toBeChecked();
    sessionStorage.removeItem(firstDraft);
    sessionStorage.removeItem(secondDraft);
  });

  it("shows the chosen Heinzel read-only and edits its name and look in its chat", async () => {
    let stored = { ...snapshot(first, "Alpha"), messages: [], schedule: null };
    mockApi({
      ...shell,
      "/api/heinzel/sessions": { sessions },
      "/api/heinzel/session": () => stored,
      "/api/heinzel/profile": async (request: Request) => {
        const body = await request.json();
        stored = { ...stored, name: body.name, appearance: body.appearance, profile_revision: 2 };
        return stored;
      },
    });
    const { container } = renderAt("/heinzel");
    const section = await page();
    fireEvent.click(await section.findByRole("radio", { name: /^Alpha/ }));
    // One editor: the page has no name field and no save, only the Heinzel and where to edit it.
    expect(await section.findByTestId("heinzel-view-name")).toHaveTextContent("Alpha");
    expect(section.queryByLabelText("Name")).toBeNull();
    expect(section.queryByRole("button", { name: "Save name and look" })).toBeNull();
    expect(section.getByText("Change the name and look in its chat.")).toBeInTheDocument();
    await expectAccessible(container);
    fireEvent.click(section.getByRole("button", { name: "Edit in chat" }));
    const chat = within(await screen.findByRole("complementary", { name: "Chat" }));
    expect(await chat.findByRole("heading", { name: "Alpha" })).toBeInTheDocument();
    fireEvent.change(await chat.findByLabelText("Name"), { target: { value: "Moss" } });
    // Choices read as words, never as the stored values.
    expect(within(chat.getByLabelText("Age")).getByRole("option", { name: "Grown" })).toHaveValue("grown");
    held(chat);
    fireEvent.click(chat.getByRole("button", { name: "Save name and look" }));
    expect(await chat.findByText("Saved.")).toBeInTheDocument();
    await waitFor(() => expect(section.getByTestId("heinzel-view-name")).toHaveTextContent("Moss"));
  });

  it("retires a Heinzel, keeps it under Retired and restores it", async () => {
    let state = "proposed";
    const posts: string[] = [];
    const list = () => ({
      sessions: [{ ...sessions[0], state }, sessions[1]],
    });
    mockApi({
      ...shell,
      "/api/heinzel/sessions": list,
      "/api/heinzel/session": () => ({ ...snapshot(first, "Alpha"), state }),
      "/api/heinzel/retire": async (request: Request) => {
        const body = await request.json();
        posts.push(`retire:${body.session_id}`);
        state = "retired";
        return { ...snapshot(first, "Alpha"), state };
      },
      "/api/heinzel/restore": async (request: Request) => {
        const body = await request.json();
        posts.push(`restore:${body.session_id}`);
        state = "proposed";
        return { ...snapshot(first, "Alpha"), state };
      },
    });
    const { container } = renderAt(`/heinzel?chat=${first}`);
    const section = await page();
    fireEvent.click(await section.findByRole("radio", { name: /^Alpha/ }));
    fireEvent.click(await section.findByRole("button", { name: "Retire" }));
    expect(await section.findByText("Alpha is retired. Restore it under Retired.")).toBeInTheDocument();
    await waitFor(() => expect(section.queryByRole("radio", { name: /^Alpha/ })).toBeNull());
    expect(section.getByRole("radio", { name: /^Beta/ })).toBeInTheDocument();
    // Its open conversation closes with it.
    await waitFor(() => expect(screen.queryByRole("heading", { name: "Alpha" })).toBeNull());
    expect(section.getByText("Retired (1)")).toBeInTheDocument();
    await expectAccessible(container);
    fireEvent.click(section.getByRole("button", { name: "Restore Alpha" }));
    expect(await section.findByRole("radio", { name: /^Alpha/ })).toBeInTheDocument();
    await waitFor(() => expect(section.queryByText("Retired (1)")).toBeNull());
    expect(posts).toEqual([`retire:${first}`, `restore:${first}`]);
  });

  it("says when a retire or a restore did not happen", async () => {
    mockApi({
      ...shell,
      "/api/heinzel/sessions": { sessions: [{ ...sessions[0], state: "retired" }, sessions[1]] },
      "/api/heinzel/session": snapshot(second, "Beta"),
      "/api/heinzel/retire": { status: 503, body: {} },
      "/api/heinzel/restore": { status: 503, body: {} },
    });
    renderAt("/heinzel");
    const section = await page();
    fireEvent.click(await section.findByRole("radio", { name: /^Beta/ }));
    fireEvent.click(await section.findByRole("button", { name: "Retire" }));
    expect(await section.findByText("Could not retire. Try again.")).toBeInTheDocument();
    fireEvent.click(section.getByText("Retired (1)"));
    fireEvent.click(section.getByRole("button", { name: "Restore Alpha" }));
    expect(await section.findByText("Could not restore. Try again.")).toBeInTheDocument();
  });

  it("creates nothing until the styled draft is saved, then lands in its chat for the schedule", async () => {
    let stored = {
      ...snapshot(first, "New Heinzel"),
      messages: [],
      schedule: null as null | typeof checkInSchedule | (typeof checkInSchedule & { status: "active"; revision: 2 }),
    };
    const review = {
      id: "0123456789abcdef",
      personal_session_id: first,
      title: "Check-in approval",
      messages: [],
      actions: [
        {
          id: "schedule-action",
          tool: "heinzel_schedule_control",
          module: "heinzel",
          operation: "schedule-control",
          command: "hz heinzel schedule-control",
          arguments: {
            session_id: first,
            request_id: "approval-request",
            expected_revision: 1,
            action: "activate",
            expected_contract_version: 1,
            expected_preview_hash: checkInSchedule.preview_hash,
          },
          status: "pending",
        },
      ],
      costs: { spent_usd: 0, session_max_usd: 2, message_max_usd: 0.2 },
    };
    const created: Record<string, unknown>[] = [];
    mockApi({
      ...shell,
      "/api/heinzel/sessions": { sessions: [] },
      "/api/heinzel/new-chat": async (request: Request) => {
        const body = await request.json();
        created.push(body);
        stored = { ...stored, name: body.name, appearance: body.appearance };
        return stored;
      },
      "/api/heinzel/session": () => stored,
      "/api/heinzel/profile": async (request: Request) => {
        const body = await request.json();
        stored = { ...stored, name: body.name, appearance: body.appearance, profile_revision: 2 };
        return stored;
      },
      "/api/heinzel/propose-check-in": () => {
        stored = { ...stored, schedule: checkInSchedule };
        return stored;
      },
      "/api/system/runner/chat/propose-schedule-control": review,
      "/api/system/runner/chat/confirm": () => {
        stored = { ...stored, schedule: { ...checkInSchedule, status: "active", revision: 2 } };
        return {
          ...review,
          actions: [{ ...review.actions[0], status: "done", result: "schedule active" }],
        };
      },
    });
    renderAt("/heinzel");
    const section = within(await screen.findByRole("region", { name: "Personal Heinzels" }));
    fireEvent.click(await section.findByRole("button", { name: "Create a personal Heinzel" }));
    // The draft is local: it has a preview and no name yet, and nothing was sent.
    const save = await section.findByRole("button", { name: "Save and chat" });
    expect(save).toBeDisabled();
    fireEvent.change(section.getByLabelText("Name"), { target: { value: "Moss" } });
    fireEvent.click(swatch(section, "Hat colour", "Teal"));
    expect(section.getByRole("img", { name: /Appearance preview/ }).querySelector('[fill="#1F9488"]')).not.toBeNull();
    expect(created).toHaveLength(0);
    fireEvent.click(save);
    await waitFor(() => expect(created).toHaveLength(1));
    expect(created[0]).toMatchObject({
      request_id: expect.any(String),
      name: "Moss",
      appearance: { age: "grown", build: "regular", hat: "teal" },
    });
    expect(sessionStorage.getItem("hz.personal-heinzel.new-draft")).toBeNull();
    const chat = within(await screen.findByRole("complementary", { name: "Chat" }));
    expect(await chat.findByRole("heading", { name: "Moss" })).toBeInTheDocument();
    fireEvent.click(await chat.findByRole("button", { name: "Propose check-in schedule" }));
    fireEvent.click(await chat.findByRole("button", { name: "Review activation in chat" }));
    fireEvent.click(await chat.findByRole("button", { name: "Confirm" }));
    expect(await chat.findByText("active")).toBeInTheDocument();
  });

  it("retries an uncertain save using exactly the same id and payload", async () => {
    const attempts: unknown[] = [];
    mockApi({
      ...shell,
      "/api/heinzel/sessions": { sessions },
      "/api/heinzel/session": snapshot(first, "Alpha"),
      "/api/heinzel/profile": async (request: Request) => {
        const body = await request.json();
        attempts.push(body);
        return attempts.length === 1
          ? { status: 503, body: { error: "unknown" } }
          : { ...snapshot(first, body.name), appearance: body.appearance, profile_revision: 2 };
      },
    });
    renderAt("/heinzel");
    const section = await openAlpha();
    fireEvent.change(await section.findByLabelText("Name"), { target: { value: "New name" } });
    fireEvent.click(section.getByRole("button", { name: "Save name and look" }));
    const retry = await section.findByRole("button", { name: "Retry save" });
    held(section);
    fireEvent.click(retry);
    await waitFor(() => expect(attempts).toHaveLength(2));
    expect(attempts[1]).toEqual(attempts[0]);
    await waitFor(() => expect(section.getByRole("button", { name: "Back to chat" })).toBeEnabled());
  });

  it("restores an uncertain profile save after reload and retries the exact request", async () => {
    const attempts: unknown[] = [];
    const key = `hz.personal-heinzel.profile-save.${first}`;
    sessionStorage.removeItem(key);
    mockApi({
      ...shell,
      "/api/heinzel/sessions": { sessions },
      "/api/heinzel/session": snapshot(first, "Alpha"),
      "/api/heinzel/profile": async (request: Request) => {
        const body = await request.json();
        attempts.push(body);
        return attempts.length === 1
          ? { status: 503, body: { detail: "acknowledgement lost" } }
          : { ...snapshot(first, "Reloaded Alpha"), appearance: look, profile_revision: 2 };
      },
    });
    const firstView = renderAt("/heinzel");
    let section = await openAlpha();
    fireEvent.change(await section.findByLabelText("Name"), { target: { value: "Reloaded Alpha" } });
    fireEvent.click(section.getByRole("button", { name: "Save name and look" }));
    await section.findByRole("button", { name: "Retry save" });
    expect(sessionStorage.getItem(key)).not.toBeNull();
    firstView.unmount();

    renderAt("/heinzel");
    section = await openAlpha();
    fireEvent.click(await section.findByRole("button", { name: "Retry save" }));
    await waitFor(() => expect(attempts).toHaveLength(2));
    expect(attempts[1]).toEqual(attempts[0]);
    await waitFor(() => expect(sessionStorage.getItem(key)).toBeNull());
  });

  it("retains a committed but unacknowledged save after reload without sending it again", async () => {
    let attempts = 0;
    const key = `hz.personal-heinzel.profile-save.${first}`;
    sessionStorage.removeItem(key);
    let stored = snapshot(first, "Alpha");
    mockApi({
      ...shell,
      "/api/heinzel/sessions": { sessions },
      "/api/heinzel/session": () => stored,
      "/api/heinzel/profile": async (request: Request) => {
        attempts += 1;
        const body = await request.json();
        stored = { ...snapshot(first, body.name), appearance: body.appearance, profile_revision: 2 };
        return { status: 503, body: { detail: "acknowledgement lost after commit" } };
      },
    });
    const firstView = renderAt("/heinzel");
    let section = await openAlpha();
    fireEvent.change(await section.findByLabelText("Name"), { target: { value: "Committed Alpha" } });
    fireEvent.click(section.getByRole("button", { name: "Save name and look" }));
    await section.findByRole("button", { name: "Retry save" });
    firstView.unmount();

    renderAt("/heinzel");
    section = await openAlpha();
    await waitFor(() => expect(section.getByLabelText("Name")).toHaveValue("Committed Alpha"));
    expect(section.getByRole("button", { name: "Retry save" })).toBeEnabled();
    expect(attempts).toBe(1);
    expect(sessionStorage.getItem(key)).not.toBeNull();
  });

  it("allows an explicit retry when a profile changed while an old save was unresolved", async () => {
    const attempts: unknown[] = [];
    const key = `hz.personal-heinzel.profile-save.${first}`;
    sessionStorage.setItem(
      key,
      JSON.stringify({ request_id: "uncertain-save", name: "Old draft", appearance: look, expected_revision: 1 }),
    );
    mockApi({
      ...shell,
      "/api/heinzel/sessions": { sessions },
      "/api/heinzel/session": { ...snapshot(first, "Updated elsewhere"), profile_revision: 2 },
      "/api/heinzel/profile": async (request: Request) => {
        attempts.push(await request.json());
        return { ...snapshot(first, "Updated elsewhere"), profile_revision: 3 };
      },
    });
    renderAt("/heinzel");
    const section = await openAlpha();
    expect(await section.findByText(/profile changed while this save was unresolved/i)).toBeInTheDocument();
    expect(section.getByRole("button", { name: "Retry save" })).toBeEnabled();
    expect(attempts).toHaveLength(0);
    fireEvent.click(section.getByRole("button", { name: "Check saved profile" }));
    await waitFor(() => expect(section.getByLabelText("Name")).toHaveValue("Updated elsewhere"));
    expect(sessionStorage.getItem(key)).toBeNull();
  });

  it("keeps a pending save when a reload sees the original revision", async () => {
    const key = `hz.personal-heinzel.profile-save.${first}`;
    const request = { request_id: "delayed-save", name: "Delayed", appearance: look, expected_revision: 1 };
    sessionStorage.setItem(key, JSON.stringify(request));
    let attempts = 0;
    mockApi({
      ...shell,
      "/api/heinzel/sessions": { sessions },
      "/api/heinzel/session": snapshot(first, "Alpha"),
      "/api/heinzel/profile": () => {
        attempts += 1;
        return { status: 503, body: {} };
      },
    });
    renderAt("/heinzel");
    const section = await openAlpha();
    fireEvent.click(await section.findByRole("button", { name: "Check saved profile" }));
    await section.findByText(/could not confirm the save/i);
    expect(JSON.parse(sessionStorage.getItem(key) ?? "null")).toEqual(request);
    expect(section.getByLabelText("Name")).toHaveValue("Delayed");
    held(section);
    expect(attempts).toBe(0);
  });

  it("keeps the creation request and its exact name and look when a response is lost", async () => {
    const attempts: unknown[] = [];
    mockApi({
      ...shell,
      "/api/heinzel/sessions": { sessions },
      "/api/heinzel/new-chat": async (request: Request) => {
        const body = await request.json();
        attempts.push(body);
        return attempts.length === 1 ? { status: 503, body: { detail: "unknown" } } : snapshot(first, body.name);
      },
      "/api/heinzel/session": snapshot(first, "Fern"),
    });
    const mounted = renderAt("/heinzel?create=1");
    let section = within(await screen.findByRole("region", { name: "Personal Heinzels" }));
    fireEvent.change(await section.findByLabelText("Name"), { target: { value: "Fern" } });
    fireEvent.click(section.getByLabelText("Umbrella"));
    fireEvent.click(section.getByRole("button", { name: "Save and chat" }));
    await section.findByRole("button", { name: "Retry creation" });
    expect(section.getByRole("alert")).toHaveTextContent(/Could not confirm creation/);
    expect(section.getByLabelText("Name")).toBeDisabled();
    mounted.unmount();
    // A reload brings the unconfirmed draft back by itself, so the retry is the same request.
    renderAt("/heinzel");
    section = within(await screen.findByRole("region", { name: "Personal Heinzels" }));
    fireEvent.click(await section.findByRole("button", { name: "Retry creation" }));
    await waitFor(() => expect(attempts).toHaveLength(2));
    expect(attempts[1]).toEqual(attempts[0]);
    expect(attempts[0]).toMatchObject({ name: "Fern", appearance: { accessories: ["umbrella"] } });
    await waitFor(() => expect(sessionStorage.getItem("hz.personal-heinzel.new-draft")).toBeNull());
  });

  it("discards a draft and the message it holds without creating anything", async () => {
    sessionStorage.setItem("hz.personal-heinzel.new-draft-message", "Remind me on Fridays");
    const { calls } = mockApi({ ...shell, "/api/heinzel/sessions": { sessions } });
    const { container } = renderAt("/heinzel?create=1");
    const section = within(await screen.findByRole("region", { name: "Personal Heinzels" }));
    fireEvent.change(await section.findByLabelText("Name"), { target: { value: "Passing thought" } });
    await waitFor(() => expect(sessionStorage.getItem("hz.personal-heinzel.new-draft")).not.toBeNull());
    await expectAccessible(container);
    fireEvent.click(section.getByRole("button", { name: "Discard draft" }));
    expect(await section.findByRole("button", { name: "Create a personal Heinzel" })).toBeEnabled();
    expect(section.queryByLabelText("Name")).not.toBeInTheDocument();
    expect(sessionStorage.getItem("hz.personal-heinzel.new-draft")).toBeNull();
    expect(sessionStorage.getItem("hz.personal-heinzel.new-draft-message")).toBeNull();
    expect(calls.some((call) => new URL(call.url).pathname.endsWith("/heinzel/new-chat"))).toBe(false);
  });

  it("reloads the server profile after a stale revision without overwriting it", async () => {
    let loads = 0;
    mockApi({
      ...shell,
      "/api/heinzel/sessions": { sessions },
      "/api/heinzel/session": () => {
        loads += 1;
        return loads === 1
          ? snapshot(first, "Alpha")
          : { ...snapshot(first, "Updated elsewhere"), profile_revision: 2 };
      },
      "/api/heinzel/profile": { status: 400, body: { error: "This Heinzel's profile changed" } },
    });
    const { container } = renderAt("/heinzel");
    const section = await openAlpha();
    fireEvent.change(await section.findByLabelText("Name"), { target: { value: "My draft" } });
    fireEvent.click(section.getByRole("button", { name: "Save name and look" }));
    fireEvent.click(await section.findByRole("button", { name: "Check saved profile" }));
    await waitFor(() => expect(section.getByLabelText("Name")).toHaveValue("Updated elsewhere"));
    await expectAccessible(container);
  });
  it("keeps an uncertain save when an acknowledgement has the wrong identity", async () => {
    mockApi({
      ...shell,
      "/api/heinzel/sessions": { sessions },
      "/api/heinzel/session": snapshot(first, "Alpha"),
      "/api/heinzel/profile": { ...snapshot(second, "Wrong"), profile_revision: 2 },
    });
    renderAt("/heinzel");
    const section = await openAlpha();
    await section.findByLabelText("Name");
    fireEvent.click(section.getByRole("button", { name: "Save name and look" }));
    expect(await section.findByRole("button", { name: "Retry save" })).toBeInTheDocument();
    held(section);
  });
  it("keeps the pending save when the acknowledgement contains different profile values", async () => {
    mockApi({
      ...shell,
      "/api/heinzel/sessions": { sessions },
      "/api/heinzel/session": snapshot(first, "Alpha"),
      "/api/heinzel/profile": { ...snapshot(first, "Other profile"), profile_revision: 2 },
    });
    renderAt("/heinzel");
    const section = await openAlpha();
    fireEvent.change(await section.findByLabelText("Name"), { target: { value: "My draft" } });
    fireEvent.click(section.getByRole("button", { name: "Save name and look" }));
    expect(await section.findByRole("button", { name: "Retry save" })).toBeInTheDocument();
    expect(section.getByLabelText("Name")).toHaveValue("My draft");
    held(section);
  });
});
