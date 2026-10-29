import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { expectAccessible, mockApi } from "@/test/render";
import { SkillDraftPanel } from "./SkillDraftPanel";

const sessionId = "hz-01ARZ3NDEKTSV4RRFFQ69G5FAV";
afterEach(() => sessionStorage.clear());

describe("private skill draft review", () => {
  it("reviews exact files and sharing intent before saving without publishing", async () => {
    let stored: Record<string, unknown> | null = null;
    const { calls } = mockApi({
      "/api/heinzel/propose-skill-draft": async (request: Request) => {
        const body = await request.clone().json();
        stored = { ...body, status: "draft", preview_sha256: "a".repeat(64) };
        return stored;
      },
      "/api/heinzel/skill-draft": () => stored,
    });
    const user = userEvent.setup();
    const { container } = render(<SkillDraftPanel sessionId={sessionId} />);
    await user.click(screen.getByText("Skill draft"));
    await user.type(screen.getByLabelText("Skill name"), "review-work");
    await user.selectOptions(screen.getByLabelText("Requested sharing scope"), "team");
    await user.type(screen.getByLabelText("Scope reference"), "team-1");
    fireEvent.change(within(screen.getByRole("group", { name: "File 1" })).getByLabelText("Contents"), {
      target: { value: "# Instructions\nPrivate draft" },
    });
    await user.click(screen.getByRole("button", { name: "Add supporting file" }));
    const script = screen.getByRole("group", { name: "File 2" });
    await user.type(within(script).getByLabelText("Path"), "scripts/check.py");
    fireEvent.change(within(script).getByLabelText("Contents"), { target: { value: "print('review only')\n" } });
    await user.click(screen.getByRole("button", { name: "Review draft" }));
    expect(screen.getByRole("region", { name: "Review skill draft" })).toHaveTextContent("team · team-1 (not shared)");
    expect(screen.getByText("scripts/check.py")).toBeInTheDocument();
    expect(screen.getByText("print('review only')")).toBeInTheDocument();
    expect(calls).toHaveLength(0);
    await expectAccessible(container);
    await user.click(screen.getByRole("button", { name: "Save private draft" }));
    expect(await screen.findByText("Saved private draft: review-work")).toBeVisible();
    const post = calls.find((request) => new URL(request.url).pathname.endsWith("/propose-skill-draft"));
    expect(await post?.clone().json()).toMatchObject({
      session_id: sessionId,
      name: "review-work",
      requested_scope: "team",
      scope_ref: "team-1",
      files: { "SKILL.md": "# Instructions\nPrivate draft", "scripts/check.py": "print('review only')\n" },
    });
    expect(calls).toHaveLength(1);
    expect(sessionStorage.getItem(`hz.skill-draft.pending.${sessionId}`)).toBeNull();
  });

  it("recovers an uncertain save across reload with the same request and exact files", async () => {
    let stored: Record<string, unknown> | null = null;
    let attempts = 0;
    const { calls } = mockApi({
      "/api/heinzel/propose-skill-draft": async (request: Request) => {
        const body = await request.clone().json();
        stored ??= { ...body, status: "draft", preview_sha256: "b".repeat(64) };
        attempts += 1;
        return attempts === 1 ? { status: 503, body: { error: "unknown" } } : stored;
      },
      "/api/heinzel/skill-draft": () => stored,
    });
    const user = userEvent.setup();
    const first = render(<SkillDraftPanel sessionId={sessionId} />);
    await user.click(screen.getByText("Skill draft"));
    await user.type(screen.getByLabelText("Skill name"), "review-work");
    fireEvent.change(screen.getByLabelText("Contents"), { target: { value: "review carefully" } });
    await user.click(screen.getByRole("button", { name: "Review draft" }));
    await user.click(screen.getByRole("button", { name: "Save private draft" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Retry the same request");
    first.unmount();
    const second = render(<SkillDraftPanel sessionId={sessionId} />);
    expect(await screen.findByRole("region", { name: "Review skill draft" })).toHaveTextContent("review carefully");
    await user.click(screen.getByRole("button", { name: "Retry same draft" }));
    expect(await screen.findByText("Saved private draft: review-work")).toBeVisible();
    const posts = calls.filter((request) => new URL(request.url).pathname.endsWith("/propose-skill-draft"));
    expect(posts).toHaveLength(2);
    const [one, two] = posts;
    if (!one || !two) throw new Error("Missing attempts");
    expect(await one.clone().json()).toEqual(await two.clone().json());
    second.unmount();
    render(<SkillDraftPanel sessionId={sessionId} />);
    await user.click(screen.getByText("Skill draft"));
    await user.click(screen.getByRole("button", { name: "Open saved draft" }));
    expect(await screen.findByText("Saved private draft: review-work")).toBeVisible();
  });

  it("rejects traversal before a request and never overwrites a pending draft", async () => {
    const { calls } = mockApi({});
    const user = userEvent.setup();
    render(<SkillDraftPanel sessionId={sessionId} />);
    await user.click(screen.getByText("Skill draft"));
    await user.type(screen.getByLabelText("Skill name"), "review-work");
    fireEvent.change(screen.getByLabelText("Contents"), { target: { value: "draft" } });
    await user.click(screen.getByRole("button", { name: "Add supporting file" }));
    const file = screen.getByRole("group", { name: "File 2" });
    await user.type(within(file).getByLabelText("Path"), "scripts/../escape.py");
    fireEvent.change(within(file).getByLabelText("Contents"), { target: { value: "no" } });
    await user.click(screen.getByRole("button", { name: "Review draft" }));
    expect(screen.getByRole("alert")).toHaveTextContent("file paths");
    expect(calls).toHaveLength(0);
  });
});

it("fails closed when a pending draft cannot be recovered", async () => {
  sessionStorage.setItem(`hz.skill-draft.pending.${sessionId}`, "not valid JSON");
  const { calls } = mockApi({});
  render(<SkillDraftPanel sessionId={sessionId} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("cannot be recovered");
  expect(screen.getByRole("button", { name: "Review draft" })).toBeDisabled();
  expect(sessionStorage.getItem(`hz.skill-draft.pending.${sessionId}`)).toBe("not valid JSON");
  expect(calls).toHaveLength(0);
});

it("refuses a different session's draft and prevents editing during lookup", async () => {
  let finish: ((value: unknown) => void) | undefined;
  const { calls } = mockApi({
    "/api/heinzel/skill-draft": () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  });
  const user = userEvent.setup();
  render(<SkillDraftPanel sessionId={sessionId} />);
  await user.click(screen.getByText("Skill draft"));
  await user.type(screen.getByLabelText("Open a saved draft by ID"), "draft-1");
  await user.click(screen.getByRole("button", { name: "Open saved draft" }));
  await waitFor(() => expect(finish).toBeDefined());
  expect(screen.getByLabelText("Skill name")).toBeDisabled();
  finish?.({
    session_id: "hz-01ARZ3NDEKTSV4RRFFQ69G5FAW",
    request_id: "draft-1",
    name: "private-work",
    requested_scope: "personal",
    scope_ref: null,
    status: "draft",
    preview_sha256: "a".repeat(64),
    files: { "SKILL.md": "another owner's text" },
  });
  expect(await screen.findByRole("alert")).toHaveTextContent("could not be loaded");
  expect(screen.queryByText("another owner's text")).not.toBeInTheDocument();
  expect(calls.some((call) => new URL(call.url).pathname.endsWith("/propose-skill-draft"))).toBe(false);
});
