import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { shell } from "@/test/fixtures";
import { expectAccessible, mockApi, renderAt } from "@/test/render";

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
    const section = within(await screen.findByRole("region", { name: "Personal Heinzels" }));
    fireEvent.change(await section.findByLabelText("Personal Heinzel"), { target: { value: first } });
    const name = await section.findByLabelText("Name");
    fireEvent.change(name, { target: { value: "Named helper" } });
    fireEvent.change(section.getByLabelText("Hat colour"), { target: { value: "blue" } });
    expect(section.getByRole("img", { name: /Appearance preview/ })).toBeInTheDocument();
    fireEvent.click(section.getByRole("button", { name: "Save name and look" }));
    await waitFor(() => expect(saved).toHaveLength(1));
    expect(saved[0]).toMatchObject({
      session_id: first,
      name: "Named helper",
      expected_revision: 1,
      appearance: { hat: "blue", accessories: ["scarf"] },
    });
    await waitFor(() => expect(section.getByLabelText("Personal Heinzel")).not.toBeDisabled());
    fireEvent.change(section.getByLabelText("Personal Heinzel"), { target: { value: second } });
    await waitFor(() => expect(section.getByLabelText("Name")).toHaveValue("Beta"));
    expect(section.getByLabelText("Hat colour")).toHaveValue("");
    fireEvent.change(section.getByLabelText("Personal Heinzel"), { target: { value: first } });
    await waitFor(() => expect(section.getByLabelText("Name")).toHaveValue("Named helper"));
    expect(section.getByLabelText("Hat colour")).toHaveValue("blue");
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
    const section = within(await screen.findByRole("region", { name: "Personal Heinzels" }));
    fireEvent.change(await section.findByLabelText("Personal Heinzel"), { target: { value: first } });
    const scarf = await section.findByLabelText("scarf");
    expect(scarf).toBeChecked();
    fireEvent.click(scarf);
    fireEvent.click(section.getByLabelText("umbrella"));
    fireEvent.click(section.getByLabelText("sunglasses"));
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
    let section = within(await screen.findByRole("region", { name: "Personal Heinzels" }));
    fireEvent.change(await section.findByLabelText("Personal Heinzel"), { target: { value: first } });
    const name = await section.findByLabelText("Name");
    fireEvent.change(name, { target: { value: "Styled Alpha" } });
    fireEvent.click(section.getByLabelText("umbrella"));
    await waitFor(() => expect(sessionStorage.getItem(firstDraft)).not.toBeNull());
    firstView.unmount();

    renderAt("/heinzel");
    section = within(await screen.findByRole("region", { name: "Personal Heinzels" }));
    fireEvent.change(await section.findByLabelText("Personal Heinzel"), { target: { value: first } });
    await waitFor(() => expect(section.getByLabelText("Name")).toHaveValue("Styled Alpha"));
    expect(section.getByLabelText("umbrella")).toBeChecked();
    fireEvent.change(section.getByLabelText("Personal Heinzel"), { target: { value: second } });
    await waitFor(() => expect(section.getByLabelText("Name")).toHaveValue("Beta"));
    expect(section.getByLabelText("umbrella")).not.toBeChecked();
    sessionStorage.removeItem(firstDraft);
    sessionStorage.removeItem(secondDraft);
  });

  it("continues to schedule setup only after the selected profile is saved", async () => {
    let stored = { ...snapshot(first, "Alpha"), messages: [], schedule: null };
    mockApi({
      ...shell,
      "/api/heinzel/sessions": { sessions },
      "/api/heinzel/session": () => stored,
      "/api/heinzel/profile": async (request: Request) => {
        const body = await request.json();
        stored = {
          ...stored,
          name: body.name,
          appearance: body.appearance,
          profile_revision: 2,
        };
        return stored;
      },
    });
    renderAt("/heinzel");
    const section = within(await screen.findByRole("region", { name: "Personal Heinzels" }));
    fireEvent.change(await section.findByLabelText("Personal Heinzel"), { target: { value: first } });
    fireEvent.change(await section.findByLabelText("Name"), { target: { value: "Moss" } });
    const continueButton = section.getByRole("button", { name: "Continue to schedule setup" });
    expect(continueButton).toBeDisabled();
    fireEvent.click(section.getByRole("button", { name: "Save name and look" }));
    await waitFor(() => expect(continueButton).toBeEnabled());
    fireEvent.click(continueButton);
    const chat = within(await screen.findByRole("complementary", { name: "Chat" }));
    expect(await chat.findByRole("heading", { name: "Moss" })).toBeInTheDocument();
    expect(await chat.findByRole("button", { name: "Propose check-in schedule" })).toBeInTheDocument();
  });

  it("takes a new profile through create, style, schedule proposal and human confirmation", async () => {
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
    mockApi({
      ...shell,
      "/api/heinzel/sessions": { sessions: [] },
      "/api/heinzel/new-chat": stored,
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
    fireEvent.click(section.getByRole("button", { name: "Create a personal Heinzel" }));
    fireEvent.change(await section.findByLabelText("Name"), { target: { value: "Moss" } });
    fireEvent.change(section.getByLabelText("Hat colour"), { target: { value: "teal" } });
    fireEvent.click(section.getByRole("button", { name: "Save name and look" }));
    const continueButton = await section.findByRole("button", { name: "Continue to schedule setup" });
    await waitFor(() => expect(continueButton).toBeEnabled());
    fireEvent.click(continueButton);
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
    const section = within(await screen.findByRole("region", { name: "Personal Heinzels" }));
    fireEvent.change(await section.findByLabelText("Personal Heinzel"), { target: { value: first } });
    fireEvent.change(await section.findByLabelText("Name"), { target: { value: "New name" } });
    fireEvent.click(section.getByRole("button", { name: "Save name and look" }));
    const retry = await section.findByRole("button", { name: "Retry save" });
    expect(section.getByLabelText("Personal Heinzel")).toBeDisabled();
    fireEvent.click(retry);
    await waitFor(() => expect(attempts).toHaveLength(2));
    expect(attempts[1]).toEqual(attempts[0]);
    await waitFor(() => expect(section.getByLabelText("Personal Heinzel")).not.toBeDisabled());
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
    let section = within(await screen.findByRole("region", { name: "Personal Heinzels" }));
    fireEvent.change(await section.findByLabelText("Personal Heinzel"), { target: { value: first } });
    fireEvent.change(await section.findByLabelText("Name"), { target: { value: "Reloaded Alpha" } });
    fireEvent.click(section.getByRole("button", { name: "Save name and look" }));
    await section.findByRole("button", { name: "Retry save" });
    expect(sessionStorage.getItem(key)).not.toBeNull();
    firstView.unmount();

    renderAt("/heinzel");
    section = within(await screen.findByRole("region", { name: "Personal Heinzels" }));
    fireEvent.change(await section.findByLabelText("Personal Heinzel"), { target: { value: first } });
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
    let section = within(await screen.findByRole("region", { name: "Personal Heinzels" }));
    fireEvent.change(await section.findByLabelText("Personal Heinzel"), { target: { value: first } });
    fireEvent.change(await section.findByLabelText("Name"), { target: { value: "Committed Alpha" } });
    fireEvent.click(section.getByRole("button", { name: "Save name and look" }));
    await section.findByRole("button", { name: "Retry save" });
    firstView.unmount();

    renderAt("/heinzel");
    section = within(await screen.findByRole("region", { name: "Personal Heinzels" }));
    fireEvent.change(await section.findByLabelText("Personal Heinzel"), { target: { value: first } });
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
    const section = within(await screen.findByRole("region", { name: "Personal Heinzels" }));
    fireEvent.change(await section.findByLabelText("Personal Heinzel"), { target: { value: first } });
    expect(await section.findByRole("alert")).toHaveTextContent(/profile changed while this save was unresolved/i);
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
    const section = within(await screen.findByRole("region", { name: "Personal Heinzels" }));
    fireEvent.change(await section.findByLabelText("Personal Heinzel"), { target: { value: first } });
    fireEvent.click(await section.findByRole("button", { name: "Check saved profile" }));
    await section.findByText(/could not confirm the save/i);
    expect(JSON.parse(sessionStorage.getItem(key) ?? "null")).toEqual(request);
    expect(section.getByLabelText("Name")).toHaveValue("Delayed");
    expect(section.getByLabelText("Personal Heinzel")).toBeDisabled();
    expect(attempts).toBe(0);
  });

  it("keeps the creation request id when a response is lost", async () => {
    const attempts: unknown[] = [];
    mockApi({
      ...shell,
      "/api/heinzel/sessions": { sessions },
      "/api/heinzel/new-chat": async (request: Request) => {
        const body = await request.json();
        attempts.push(body);
        return attempts.length === 1 ? { status: 503, body: { detail: "unknown" } } : snapshot(first, "New Heinzel");
      },
      "/api/heinzel/session": snapshot(first, "New Heinzel"),
    });
    const mounted = renderAt("/heinzel");
    let section = within(await screen.findByRole("region", { name: "Personal Heinzels" }));
    fireEvent.click(section.getByRole("button", { name: "Create a personal Heinzel" }));
    await section.findByRole("button", { name: "Retry creation" });
    mounted.unmount();
    renderAt("/heinzel");
    section = within(await screen.findByRole("region", { name: "Personal Heinzels" }));
    fireEvent.click(section.getByRole("button", { name: "Create a personal Heinzel" }));
    await waitFor(() => expect(attempts).toHaveLength(2));
    expect(attempts[1]).toEqual(attempts[0]);
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
    const section = within(await screen.findByRole("region", { name: "Personal Heinzels" }));
    fireEvent.change(await section.findByLabelText("Personal Heinzel"), { target: { value: first } });
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
    const section = within(await screen.findByRole("region", { name: "Personal Heinzels" }));
    fireEvent.change(await section.findByLabelText("Personal Heinzel"), { target: { value: first } });
    await section.findByLabelText("Name");
    fireEvent.click(section.getByRole("button", { name: "Save name and look" }));
    expect(await section.findByRole("button", { name: "Retry save" })).toBeInTheDocument();
    expect(section.getByLabelText("Personal Heinzel")).toBeDisabled();
  });
  it("keeps the pending save when the acknowledgement contains different profile values", async () => {
    mockApi({
      ...shell,
      "/api/heinzel/sessions": { sessions },
      "/api/heinzel/session": snapshot(first, "Alpha"),
      "/api/heinzel/profile": { ...snapshot(first, "Other profile"), profile_revision: 2 },
    });
    renderAt("/heinzel");
    const section = within(await screen.findByRole("region", { name: "Personal Heinzels" }));
    fireEvent.change(await section.findByLabelText("Personal Heinzel"), { target: { value: first } });
    fireEvent.change(await section.findByLabelText("Name"), { target: { value: "My draft" } });
    fireEvent.click(section.getByRole("button", { name: "Save name and look" }));
    expect(await section.findByRole("button", { name: "Retry save" })).toBeInTheDocument();
    expect(section.getByLabelText("Name")).toHaveValue("My draft");
    expect(section.getByLabelText("Personal Heinzel")).toBeDisabled();
  });
});
