import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it } from "vitest";
import type { SchemaSkillDraft } from "@/api/client";
import { expectAccessible, mockApi } from "@/test/render";
import { SkillSharingPanel } from "./SkillSharingPanel";

const draft: SchemaSkillDraft = {
  session_id: "hz-01ARZ3NDEKTSV4RRFFQ69G5FAV",
  request_id: "draft-1",
  name: "review-work",
  requested_scope: "team",
  scope_ref: "team-1",
  files: { "SKILL.md": "private instructions" },
  status: "draft",
  preview_sha256: "a".repeat(64),
};
afterEach(() => sessionStorage.clear());

it("requests an exact private sharing review without granting access", async () => {
  const { calls } = mockApi({
    "/api/heinzel/propose-skill-sharing": async (request: Request) => ({
      ...(await request.clone().json()),
      requested_scope: "team",
      scope_ref: "team-1",
      status: "awaiting_review",
      authority: "unverified",
    }),
  });
  const user = userEvent.setup();
  const { container } = render(<SkillSharingPanel draft={draft} />);
  expect(screen.getByText("Target: team · team-1")).toBeVisible();
  expect(screen.getByText(/Membership and authority are unverified/)).toBeVisible();
  await expectAccessible(container);
  await user.click(screen.getByRole("button", { name: "Request review of sharing" }));
  expect(await screen.findByRole("status")).toHaveTextContent("awaiting review; authority unverified. Not shared.");
  const payload = await calls[0]?.clone().json();
  expect(payload).toMatchObject({
    session_id: draft.session_id,
    draft_request_id: draft.request_id,
    draft_sha256: draft.preview_sha256,
  });
  expect(JSON.stringify(payload)).not.toContain("private instructions");
});

it("recovers an uncertain request on reload and discovers it later", async () => {
  let saved: Record<string, unknown> | null = null;
  let attempts = 0;
  const { calls } = mockApi({
    "/api/heinzel/propose-skill-sharing": async (request: Request) => {
      const body = await request.clone().json();
      saved ??= {
        ...body,
        requested_scope: "team",
        scope_ref: "team-1",
        status: "awaiting_review",
        authority: "unverified",
      };
      attempts++;
      return attempts === 1 ? { status: 503, body: { error: "unknown" } } : saved;
    },
    "/api/heinzel/skill-sharing-proposals": () => ({
      items: [{ request_id: saved?.request_id, seq: 3 }],
      next_cursor: null,
    }),
    "/api/heinzel/skill-sharing": () => saved,
  });
  const user = userEvent.setup();
  const first = render(<SkillSharingPanel draft={draft} />);
  await user.click(screen.getByRole("button", { name: "Request review of sharing" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Retry the same request");
  const retained = sessionStorage.getItem(`hz.skill-sharing.pending.${draft.session_id}.${draft.request_id}`);
  expect(retained).not.toContain("private instructions");
  first.unmount();
  const second = render(<SkillSharingPanel draft={draft} />);
  await user.click(screen.getByRole("button", { name: "Retry same sharing request" }));
  expect(await screen.findByRole("status")).toHaveTextContent("Not shared");
  const posts = calls.filter((call) => new URL(call.url).pathname.endsWith("propose-skill-sharing"));
  expect(await posts[0]?.clone().json()).toEqual(await posts[1]?.clone().json());
  second.unmount();
  render(<SkillSharingPanel draft={draft} />);
  await user.click(screen.getByRole("button", { name: "Load sharing requests" }));
  const open = await screen.findByRole("button", { name: /^Open request/ });
  await user.click(open);
  expect(await screen.findByRole("status")).toHaveTextContent("awaiting review");
});

it("blocks corrupted pending state", async () => {
  sessionStorage.setItem(`hz.skill-sharing.pending.${draft.session_id}.${draft.request_id}`, "broken");
  const { calls } = mockApi({});
  render(<SkillSharingPanel draft={draft} />);
  expect(screen.getByRole("alert")).toHaveTextContent("cannot be recovered");
  expect(screen.getByRole("button", { name: "Request review of sharing" })).toBeDisabled();
  expect(calls).toHaveLength(0);
});

it("does not accept a response for another target", async () => {
  mockApi({
    "/api/heinzel/propose-skill-sharing": async (request: Request) => ({
      ...(await request.clone().json()),
      requested_scope: "team",
      scope_ref: "different-team",
      status: "awaiting_review",
      authority: "unverified",
    }),
  });
  const user = userEvent.setup();
  render(<SkillSharingPanel draft={draft} />);
  await user.click(screen.getByRole("button", { name: "Request review of sharing" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Could not confirm");
  expect(screen.queryByRole("status")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Retry same sharing request" })).toBeEnabled();
});

it("lists only requests for the currently displayed exact draft", async () => {
  const { calls } = mockApi({
    "/api/heinzel/skill-sharing-proposals": () => ({
      items: [
        { request_id: "other", seq: 1 },
        { request_id: "current", seq: 2 },
      ],
      next_cursor: null,
    }),
    "/api/heinzel/skill-sharing": (request: Request) => {
      const requestId = new URL(request.url).searchParams.get("request_id");
      return {
        session_id: draft.session_id,
        request_id: requestId,
        draft_request_id: requestId === "current" ? draft.request_id : "other-draft",
        draft_sha256: draft.preview_sha256,
        requested_scope: draft.requested_scope,
        scope_ref: draft.scope_ref,
        status: "awaiting_review",
        authority: "unverified",
      };
    },
  });
  const user = userEvent.setup();
  render(<SkillSharingPanel draft={draft} />);
  await user.click(screen.getByRole("button", { name: "Load sharing requests" }));
  expect(await screen.findByRole("button", { name: "Open request current" })).toBeVisible();
  expect(screen.queryByRole("button", { name: "Open request other" })).not.toBeInTheDocument();
  expect(calls.filter((call) => new URL(call.url).pathname.endsWith("skill-sharing"))).toHaveLength(2);
});
