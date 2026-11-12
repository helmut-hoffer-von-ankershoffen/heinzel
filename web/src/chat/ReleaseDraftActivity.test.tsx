import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { SchemaChatAction } from "@/api/client";
import { authorityEpoch } from "@/api/token";
import { mockApi } from "@/test/render";
import { ReleaseDraftActivity, ReleaseDraftReviewDetails, releaseDraftReview } from "./ReleaseDraftActivity";

const SESSION = "durable-1";
const KEY = "a".repeat(64);
const REQUEST = "b".repeat(64);

const review = () => ({
  session_id: SESSION,
  draft_key: KEY,
  snapshot: {
    draft_key: KEY,
    text: "The edited draft",
    target: { reference: "doc-7", revision: "r3", section: "Intro" },
  },
  channel: "slack",
  channel_target: { account: "work", destination: "#team", scope: "chat:write" },
  link_text: "doc-7",
});
const action = (overrides: Record<string, unknown> = {}, args: Record<string, unknown> = {}) =>
  ({
    id: "act-1",
    module: "heinzel",
    operation: "share-draft-link",
    command: "hz heinzel share-draft-link",
    status: "pending",
    arguments: { session_id: SESSION, request_id: REQUEST, review: review(), ...args },
    ...overrides,
  }) as unknown as SchemaChatAction;

describe("The review of a release draft share", () => {
  it("reads every reviewed fact out of a complete action of this session", () => {
    expect(releaseDraftReview(action(), SESSION)).toEqual({
      draftKey: KEY,
      requestId: REQUEST,
      text: "The edited draft",
      document: "doc-7",
      revision: "r3",
      section: "Intro",
      channel: "slack",
      account: "work",
      destination: "#team",
      scope: "chat:write",
      link: "doc-7",
    });
  });

  it.each([
    ["another module", { module: "other" }, {}],
    ["another operation", { operation: "accept-agent-contract" }, {}],
    ["another session in the arguments", {}, { session_id: "x" }],
    ["a request id that is not a digest", {}, { request_id: "short" }],
    ["another session in the review", {}, { review: { ...review(), session_id: "x" } }],
    ["a draft key that is not a digest", {}, { review: { ...review(), draft_key: "nope" } }],
    [
      "a snapshot of another draft",
      {},
      { review: { ...review(), snapshot: { ...review().snapshot, draft_key: "c".repeat(64) } } },
    ],
    ["an empty text", {}, { review: { ...review(), snapshot: { ...review().snapshot, text: "" } } }],
    ["no destination", {}, { review: { ...review(), channel_target: { account: "work", scope: "s" } } }],
    ["a link that is not the document", {}, { review: { ...review(), link_text: "https://elsewhere" } }],
  ])("is not offered for %s", (_name, overrides, args) => {
    expect(releaseDraftReview(action(overrides, args), SESSION)).toBeUndefined();
  });

  it("shows what will be sent, where and from which account", () => {
    const facts = releaseDraftReview(action(), SESSION);
    if (!facts) throw new Error("expected a review");
    render(<ReleaseDraftReviewDetails review={facts} />);
    for (const label of ["Document", "Reviewed revision", "Posting account", "Destination", "Posting scope"]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    expect(screen.getByText("#team")).toBeInTheDocument();
    expect(screen.getByText("The edited draft")).toBeInTheDocument();
    expect(screen.getByText(/The document can change after this review/)).toBeInTheDocument();
  });
});

describe("The release draft activity", () => {
  beforeEach(() => sessionStorage.setItem("hz.token", "test-token"));

  const mount = (props: Partial<Parameters<typeof ReleaseDraftActivity>[0]> = {}) => {
    const client = new QueryClient();
    return render(
      <QueryClientProvider client={client}>
        <ReleaseDraftActivity
          authority={authorityEpoch()}
          sessionId={SESSION}
          conversationBusy={false}
          onReview={() => {}}
          {...props}
        />
      </QueryClientProvider>,
    );
  };
  const open = async (user: ReturnType<typeof userEvent.setup>) => {
    await user.click(screen.getByText("Release draft activity"));
  };
  const activity = [
    { kind: "draft.release-observed", draft_key: KEY, at: "2026-11-11T08:00:00Z" },
    { kind: "draft.append-acknowledged", draft_key: KEY, at: "2026-11-11T09:00:00Z" },
    { kind: "draft.something-new", draft_key: KEY, at: "2026-11-11T09:30:00Z" },
    { kind: "draft.append-attempted", draft_key: "not-a-digest", at: "2026-11-11T10:00:00Z" },
  ];

  it("reads nothing until the person opens it", () => {
    const { calls } = mockApi({ "/api/heinzel/release-draft-activity": [] });
    mount();
    expect(calls).toHaveLength(0);
  });

  it("lists the newest first and hides kinds or keys it does not know", async () => {
    const user = userEvent.setup();
    mockApi({ "/api/heinzel/release-draft-activity": activity });
    mount();
    await open(user);
    const labels = await screen.findAllByRole("listitem");
    expect(labels.map((item) => item.querySelector("strong")?.textContent)).toEqual([
      "Document draft recorded",
      "Release observed",
    ]);
  });

  it("says when nothing was recorded", async () => {
    const user = userEvent.setup();
    mockApi({ "/api/heinzel/release-draft-activity": [] });
    mount();
    await open(user);
    expect(await screen.findByText("No recorded release drafts.")).toBeInTheDocument();
  });

  it("says when the activity could not be checked", async () => {
    const user = userEvent.setup();
    mockApi({ "/api/heinzel/release-draft-activity": { status: 500, body: { error: "no" } } });
    mount();
    await open(user);
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn’t check release draft activity.");
  });

  it("offers the review only on a recorded draft and not while the conversation is busy", async () => {
    const user = userEvent.setup();
    mockApi({ "/api/heinzel/release-draft-activity": activity });
    mount({ conversationBusy: true });
    await open(user);
    expect(await screen.findByRole("button", { name: "Review draft share" })).toBeDisabled();
    expect(screen.getAllByRole("button", { name: "Review draft share" })).toHaveLength(1);
  });

  it("prepares the share, proposes it and hands the conversation over for review", async () => {
    const user = userEvent.setup();
    const onReview = vi.fn();
    const chat = { id: "chat-1", personal_session_id: SESSION, actions: [action()], messages: [] };
    const { calls } = mockApi({
      "/api/heinzel/release-draft-activity": activity,
      "/api/heinzel/prepare-release-draft-share": REQUEST,
      "/api/system/runner/chat/propose-release-draft-share": chat,
    });
    mount({ onReview });
    await open(user);
    await user.click(await screen.findByRole("button", { name: "Review draft share" }));
    await waitFor(() => expect(onReview).toHaveBeenCalledWith(chat));
    const prepare = calls.find((c) => c.url.includes("prepare-release-draft-share"));
    expect(await prepare?.json()).toEqual({ session_id: SESSION, draft_key: KEY });
    const propose = calls.find((c) => c.url.includes("propose-release-draft-share"));
    expect(await propose?.json()).toEqual({ durable_session: SESSION, request_id: REQUEST });
  });

  it("does not hand over a conversation whose action is not the one prepared", async () => {
    const user = userEvent.setup();
    const onReview = vi.fn();
    mockApi({
      "/api/heinzel/release-draft-activity": activity,
      "/api/heinzel/prepare-release-draft-share": REQUEST,
      "/api/system/runner/chat/propose-release-draft-share": {
        id: "chat-1",
        personal_session_id: SESSION,
        actions: [action({}, { request_id: "c".repeat(64) })],
        messages: [],
      },
    });
    mount({ onReview });
    await open(user);
    await user.click(await screen.findByRole("button", { name: "Review draft share" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("A new review is unavailable");
    expect(onReview).not.toHaveBeenCalled();
  });

  it("says so when the review could not be opened", async () => {
    const user = userEvent.setup();
    mockApi({
      "/api/heinzel/release-draft-activity": activity,
      "/api/heinzel/prepare-release-draft-share": { status: 500, body: { error: "no" } },
    });
    mount();
    await open(user);
    await user.click(await screen.findByRole("button", { name: "Review draft share" }));
    expect(await screen.findByText("Couldn’t open the release draft review.")).toBeInTheDocument();
  });
});
