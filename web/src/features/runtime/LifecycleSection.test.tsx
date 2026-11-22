// Pause, resume, retire and restore on the Heinzel's page: pause at once with Undo; resume, retire and restore only
// after a full confirmation, and the resume only through hz's own approval of exactly that schedule.
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrictMode } from "react";
import { afterEach, describe, expect, it } from "vitest";
import { HeinzelPage } from "@/heinzel/HeinzelPage";
import { posted, renderPage, schedule, sessionId, snapshot } from "@/test/heinzelPage";
import { expectAccessible, mockApi } from "@/test/render";
import { LifecycleSection } from "./LifecycleSection";

const paused: typeof snapshot = {
  ...snapshot,
  schedule: { ...schedule, status: "paused", revision: 4, next_wake: null },
};
const approval = (status = "pending") => ({
  id: "0123456789abcdef",
  personal_session_id: sessionId,
  title: "Resume",
  messages: [],
  costs: { spent_usd: 0, session_max_usd: 2, message_max_usd: 0.2 },
  actions: [
    {
      id: "a1",
      tool: "heinzel_schedule_control",
      module: "heinzel",
      operation: "schedule-control",
      command: "hz heinzel schedule-control",
      arguments: {
        session_id: sessionId,
        request_id: "resume-1",
        expected_revision: 4,
        action: "resume",
        expected_contract_version: 1,
        expected_preview_hash: "c".repeat(64),
      },
      status,
      mcp_authorized: false,
    },
  ],
});

afterEach(() => sessionStorage.clear());

describe("pause and retire", () => {
  it("pauses at once, and Undo opens the resume for the person to confirm", async () => {
    let stored = snapshot;
    const { calls } = mockApi({
      "/api/heinzel/session": () => stored,
      "/api/heinzel/schedule-control": async (request: Request) => {
        const body = await request.json();
        expect(body).toMatchObject({ session_id: sessionId, expected_revision: 3, action: "pause" });
        expect(body).not.toHaveProperty("approval_proof");
        stored = paused;
        return paused;
      },
      "/api/system/runner/chat/propose-schedule-control": approval(),
    });
    renderPage(<HeinzelPage />);
    const section = within(await screen.findByRole("region", { name: "Pause or retire" }));
    expect(section.getByText("Running · on its schedule")).toBeInTheDocument();
    fireEvent.click(section.getByRole("button", { name: "Pause" }));
    expect(await section.findByText("Paused · nothing runs until you resume")).toBeInTheDocument();
    expect(posted(calls, "/heinzel/schedule-control")).toHaveLength(1);
    // Focus moves to what changed, since the Pause button is gone.
    await waitFor(() => expect(section.getByText("Paused · nothing runs until you resume")).toHaveFocus());
    fireEvent.click(section.getByRole("button", { name: "Undo" }));
    expect(await screen.findByRole("alertdialog", { name: "Resume Helmi?" })).toBeInTheDocument();
    await waitFor(() => expect(posted(calls, "/propose-schedule-control")).toHaveLength(1));
    expect(posted(calls, "/chat/confirm")).toHaveLength(0);
    expect(posted(calls, "/heinzel/schedule-control")).toHaveLength(1);
  });

  it("resumes only when the person confirms the whole item, through hz's approval", async () => {
    let stored = paused;
    const { calls } = mockApi({
      "/api/heinzel/session": () => stored,
      "/api/system/runner/chat/propose-schedule-control": (request: Request) => {
        const query = new URL(request.url).searchParams;
        expect(query.get("durable_session")).toBe(sessionId);
        expect(query.get("action")).toBe("resume");
        expect(query.get("expected_revision")).toBe("4");
        expect(query.get("expected_preview_hash")).toBe("c".repeat(64));
        return approval();
      },
      "/api/system/runner/chat/confirm": (request: Request) => {
        const query = new URL(request.url).searchParams;
        expect(query.get("session")).toBe("0123456789abcdef");
        expect(query.get("action")).toBe("a1");
        stored = snapshot;
        return approval("done");
      },
    });
    renderPage(<LifecycleSection snapshot={paused} />);
    const resume = screen.getByRole("button", { name: "Resume" });
    fireEvent.click(resume);
    const dialog = within(await screen.findByRole("alertdialog", { name: "Resume Helmi?" }));
    expect(dialog.getByText("What resumes: every weekday at 08:00.")).toBeInTheDocument();
    expect(dialog.getByText("Next run: the next slot after you confirm.")).toBeInTheDocument();
    // No cron text and no hash in what the person reads.
    expect(dialog.queryByText(/0 8 \* \* 1-5|cccc/)).not.toBeInTheDocument();
    // The safe choice has focus; nothing is confirmed by opening the item.
    expect(dialog.getByRole("button", { name: "Keep paused" })).toHaveFocus();
    const confirm = dialog.getByRole("button", { name: "Resume Helmi" });
    await waitFor(() => expect(confirm).toBeEnabled());
    expect(posted(calls, "/propose-schedule-control")).toHaveLength(1);
    expect(posted(calls, "/chat/confirm")).toHaveLength(0);
    await expectAccessible(document.body);
    fireEvent.click(confirm);
    await waitFor(() => expect(posted(calls, "/chat/confirm")).toHaveLength(1));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(posted(calls, "/heinzel/schedule-control")).toHaveLength(0);
    // The page reads the Heinzel again and focus lands on what changed; the confirmed approval is never declined.
    await waitFor(() => expect(screen.getByRole("status")).toHaveFocus());
    expect(posted(calls, "/chat/decline")).toHaveLength(0);
  });

  it("declines the prepared approval when the person keeps it paused", async () => {
    const { calls } = mockApi({
      "/api/heinzel/session": paused,
      "/api/system/runner/chat/propose-schedule-control": approval(),
      "/api/system/runner/chat/decline": approval("declined"),
    });
    renderPage(<LifecycleSection snapshot={paused} />);
    const resume = screen.getByRole("button", { name: "Resume" });
    fireEvent.click(resume);
    const dialog = within(await screen.findByRole("alertdialog"));
    await waitFor(() => expect(dialog.getByRole("button", { name: "Resume Helmi" })).toBeEnabled());
    fireEvent.keyDown(dialog.getByRole("button", { name: "Keep paused" }), { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(resume).toHaveFocus();
    await waitFor(() => expect(posted(calls, "/chat/decline")).toHaveLength(1));
    expect(posted(calls, "/chat/confirm")).toHaveLength(0);
  });

  it("keeps Confirm closed while hz cannot prepare the approval, and says so", async () => {
    const { calls } = mockApi({
      "/api/heinzel/session": paused,
      "/api/system/runner/chat/propose-schedule-control": { status: 409, body: { error: "stale" } },
    });
    renderPage(<LifecycleSection snapshot={paused} />);
    fireEvent.click(screen.getByRole("button", { name: "Resume" }));
    const dialog = within(await screen.findByRole("alertdialog"));
    expect(await dialog.findByRole("alert")).toHaveTextContent("That did not work. Try again.");
    expect(dialog.getByRole("button", { name: "Resume Helmi" })).toBeDisabled();
    fireEvent.click(dialog.getByRole("button", { name: "Keep paused" }));
    expect(posted(calls, "/chat/decline")).toHaveLength(0);
  });

  it("does not count a resume the runner did not finish as done", async () => {
    mockApi({
      "/api/heinzel/session": paused,
      "/api/system/runner/chat/propose-schedule-control": approval(),
      "/api/system/runner/chat/confirm": approval("failed"),
    });
    renderPage(<LifecycleSection snapshot={paused} />);
    fireEvent.click(screen.getByRole("button", { name: "Resume" }));
    const dialog = within(await screen.findByRole("alertdialog"));
    const confirm = dialog.getByRole("button", { name: "Resume Helmi" });
    await waitFor(() => expect(confirm).toBeEnabled());
    fireEvent.click(confirm);
    expect(await dialog.findByRole("alert")).toHaveTextContent("That did not work. Try again.");
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
  });

  it("retires only after its confirmation, one Heinzel at a time, and keeps its history", async () => {
    const { calls } = mockApi({
      "/api/heinzel/session": { ...snapshot, state: "retired" },
      "/api/heinzel/retire": async (request: Request) => {
        const body = await request.json();
        expect(body.session_id).toBe(sessionId);
        return { ...snapshot, state: "retired" };
      },
    });
    renderPage(<LifecycleSection snapshot={snapshot} />);
    fireEvent.click(screen.getByRole("button", { name: "Retire…" }));
    const dialog = within(await screen.findByRole("alertdialog", { name: "Retire Helmi?" }));
    expect(dialog.getByText(/never wakes/)).toBeInTheDocument();
    expect(dialog.getByText("Everything it did stays. Restore brings it back.")).toBeInTheDocument();
    expect(posted(calls, "/heinzel/retire")).toHaveLength(0);
    fireEvent.click(dialog.getByRole("button", { name: "Keep Helmi" }));
    expect(posted(calls, "/heinzel/retire")).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Retire…" }));
    fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Retire Helmi" }));
    await waitFor(() => expect(posted(calls, "/heinzel/retire")).toHaveLength(1));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    await waitFor(() => expect(screen.getByRole("status")).toHaveFocus());
  });

  it("restores a retired Heinzel after its confirmation, and has nothing to pause meanwhile", async () => {
    const retired = { ...snapshot, state: "retired" as const };
    const { calls } = mockApi({
      "/api/heinzel/session": snapshot,
      "/api/heinzel/restore": snapshot,
    });
    renderPage(<LifecycleSection snapshot={retired} />);
    expect(screen.getByText("Retired · it never wakes")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Pause" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retire…" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Restore…" }));
    const dialog = within(await screen.findByRole("alertdialog", { name: "Restore Helmi?" }));
    expect(dialog.getByText("Its schedules stay paused until you resume them.")).toBeInTheDocument();
    fireEvent.click(dialog.getByRole("button", { name: "Restore Helmi" }));
    await waitFor(() => expect(posted(calls, "/heinzel/restore")).toHaveLength(1));
  });

  it("says when a pause did not work, and leaves the Heinzel as it was", async () => {
    mockApi({ "/api/heinzel/schedule-control": { status: 409, body: { error: "stale" } } });
    renderPage(<LifecycleSection snapshot={snapshot} />);
    fireEvent.click(screen.getByRole("button", { name: "Pause" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("That did not work. Try again.");
    expect(screen.getByText("Running · on its schedule")).toBeInTheDocument();
  });

  it("has nothing to pause without a schedule", () => {
    renderPage(<LifecycleSection snapshot={{ ...snapshot, schedule: null }} />);
    expect(screen.getByText("Nothing to pause · no schedule")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Pause" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retire…" })).toBeInTheDocument();
  });

  it("says a resume it cannot confirm is unconfirmed, and offers no blind second try", async () => {
    mockApi({
      "/api/heinzel/session": paused,
      "/api/system/runner/chat/propose-schedule-control": approval(),
      "/api/system/runner/chat/confirm": approval("uncertain"),
    });
    renderPage(<LifecycleSection snapshot={paused} />);
    fireEvent.click(screen.getByRole("button", { name: "Resume" }));
    const dialog = within(await screen.findByRole("alertdialog"));
    const confirm = dialog.getByRole("button", { name: "Resume Helmi" });
    await waitFor(() => expect(confirm).toBeEnabled());
    fireEvent.click(confirm);
    expect(await screen.findByText("Resume not confirmed yet · reload before trying again")).toBeInTheDocument();
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Resume" })).not.toBeInTheDocument();
  });

  it("declines the prepared approval when the dialog goes away without Cancel", async () => {
    const { calls } = mockApi({
      "/api/heinzel/session": paused,
      "/api/system/runner/chat/propose-schedule-control": approval(),
      "/api/system/runner/chat/decline": approval("declined"),
    });
    const view = renderPage(<LifecycleSection snapshot={paused} />);
    fireEvent.click(screen.getByRole("button", { name: "Resume" }));
    const dialog = within(await screen.findByRole("alertdialog"));
    await waitFor(() => expect(dialog.getByRole("button", { name: "Resume Helmi" })).toBeEnabled());
    view.unmount();
    await waitFor(() => expect(posted(calls, "/chat/decline")).toHaveLength(1));
    expect(posted(calls, "/chat/confirm")).toHaveLength(0);
  });

  it("keeps Tab inside the confirmation", async () => {
    mockApi({});
    const user = userEvent.setup();
    renderPage(<LifecycleSection snapshot={snapshot} />);
    await user.click(screen.getByRole("button", { name: "Retire…" }));
    const dialog = await screen.findByRole("alertdialog");
    within(dialog).getByRole("button", { name: "Retire Helmi" }).focus();
    await user.tab();
    expect(within(dialog).getByRole("button", { name: "Keep Helmi" })).toHaveFocus();
    await user.tab({ shift: true });
    expect(within(dialog).getByRole("button", { name: "Retire Helmi" })).toHaveFocus();
  });

  it("declines only its own prepared approval when the dialog's effect runs twice", async () => {
    let made = 0;
    const declined: string[] = [];
    const { calls } = mockApi({
      "/api/heinzel/session": paused,
      "/api/system/runner/chat/propose-schedule-control": () => {
        made += 1;
        return { ...approval(), id: `000000000000000${made}` };
      },
      "/api/system/runner/chat/decline": (request: Request) => {
        declined.push(new URL(request.url).searchParams.get("session") ?? "");
        return approval("declined");
      },
      "/api/system/runner/chat/confirm": approval("done"),
    });
    // StrictMode mounts, unmounts and mounts again: two approvals are prepared, the first is declined, never the second.
    renderPage(
      <StrictMode>
        <LifecycleSection snapshot={paused} />
      </StrictMode>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Resume" }));
    const dialog = within(await screen.findByRole("alertdialog"));
    const confirm = dialog.getByRole("button", { name: "Resume Helmi" });
    await waitFor(() => expect(confirm).toBeEnabled());
    await waitFor(() => expect(made).toBe(2));
    await waitFor(() => expect(declined).toEqual(["0000000000000001"]));
    fireEvent.click(confirm);
    await waitFor(() => expect(posted(calls, "/chat/confirm")).toHaveLength(1));
    expect(new URL(posted(calls, "/chat/confirm")[0]?.url ?? "").searchParams.get("session")).toBe("0000000000000002");
    expect(declined).toEqual(["0000000000000001"]);
  });
});
