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
    fireEvent.click(await section.findByRole("button", { name: "Reload saved profile" }));
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
});
