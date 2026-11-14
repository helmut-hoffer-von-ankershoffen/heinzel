// Pause, resume, retire and restore on the Heinzel's page (canvas boards Profile and Lifecycle, trust rules of
// FEASIBILITY 3). Pause narrows what the Heinzel does: it happens at once and offers Undo. Resume (and so Undo of a
// pause) widens it again: it opens the whole item first, and its one confirming press is the approval that hz checks
// with its own proof (`propose-schedule-control`, then the runner chat's confirm), never a call behind the person's
// back. Retire and Restore open a full confirmation too and are never bulk. Nothing here deletes a Heinzel.
import { useQueryClient } from "@tanstack/react-query";
import { Archive, ArchiveRestore, Pause, Play, Undo2 } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { api, problem } from "@/api/client";
import type { components } from "@/api/schema";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n";
import { ConfirmDialog } from "./ConfirmDialog";
import { when } from "./when";

type Snapshot = components["schemas"]["SessionSnapshot"];
type Schedule = components["schemas"]["WakeScheduleView"];
type ChatSession = components["schemas"]["ChatSession"];
type Dialog = "resume" | "retire" | "restore" | null;

async function result<T>(call: Promise<{ data?: T; error?: unknown; response: Response }>): Promise<T> {
  const { data, error, response } = await call;
  if (!response.ok || data === undefined) throw problem(response.status, error);
  return data;
}

/** The pending resume of exactly this schedule in a runner chat's actions, if hz proposed one. */
function resumeAction(chat: ChatSession | null, sessionId: string, schedule: Schedule) {
  return (chat?.actions ?? []).find(
    (action) =>
      action.status === "pending" &&
      action.module === "heinzel" &&
      action.operation === "schedule-control" &&
      action.arguments?.session_id === sessionId &&
      action.arguments?.action === "resume" &&
      action.arguments?.expected_revision === schedule.revision,
  );
}

type Prepared = { chat: string; action: string };

/** Decline the prepared approval, so no pending resume is left behind for anyone to confirm later. */
function declinePrepared(undecided: { current: Prepared | null }) {
  const prepared = undecided.current;
  undecided.current = null;
  if (prepared)
    void api.POST("/system/runner/chat/decline", {
      params: { query: { session: prepared.chat, action: prepared.action } },
    });
}

function ResumeDialog({
  name,
  sessionId,
  schedule,
  opener,
  onDone,
  onUncertain,
  onClose,
}: {
  name: string;
  sessionId: string;
  schedule: Schedule;
  opener: React.RefObject<HTMLElement | null>;
  onDone: (snapshot: Snapshot | null) => void;
  /** hz could not say whether the resume ran: the page reloads and says so, and never offers it again blind. */
  onUncertain: () => void;
  onClose: () => void;
}) {
  // The prepared approval while it is undecided: declined if the dialog goes away by any path but Confirm.
  const undecided = useRef<Prepared | null>(null);
  const [chat, setChat] = useState<ChatSession | null>(null);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const action = resumeAction(chat, sessionId, schedule);
  // Opening the dialog asks hz to prepare the approval of exactly this schedule; nothing changes until Confirm.
  useEffect(() => {
    let live = true;
    // A fresh request each time the dialog opens: an earlier one was declined when its dialog went away.
    const request = crypto.randomUUID();
    void result(
      api.POST("/system/runner/chat/propose-schedule-control", {
        params: {
          query: {
            durable_session: sessionId,
            request_id: request,
            expected_revision: schedule.revision,
            action: "resume",
            expected_contract_version: schedule.contract_version,
            expected_preview_hash: schedule.preview_hash,
          },
        },
      }),
    ).then(
      (proposed) => {
        const prepared = resumeAction(proposed, sessionId, schedule);
        if (prepared) undecided.current = { chat: proposed.id, action: prepared.id };
        if (live) setChat(proposed);
        else declinePrepared(undecided);
      },
      () => live && setFailed(true),
    );
    return () => {
      live = false;
      declinePrepared(undecided);
    };
  }, [sessionId, schedule]);
  const confirm = async () => {
    if (!chat || !action || busy) return;
    undecided.current = null;
    setBusy(true);
    setFailed(false);
    try {
      const decided = await result(
        api.POST("/system/runner/chat/confirm", { params: { query: { session: chat.id, action: action.id } } }),
      );
      const outcome = decided.actions?.find((item) => item.id === action.id)?.status;
      if (outcome === "done") onDone(null);
      else if (outcome === "failed" || outcome === "declined") {
        setChat(decided);
        setFailed(true);
      } else onUncertain();
    } catch {
      onUncertain();
    } finally {
      setBusy(false);
    }
  };
  const next = when(schedule.next_wake);
  return (
    <ConfirmDialog
      title={t("page.resumeTitle", { name })}
      confirm={t("page.resumeConfirm", { name })}
      cancel={t("page.keepPaused")}
      opener={opener}
      busy={busy}
      ready={!!action}
      onConfirm={() => void confirm()}
      onCancel={onClose}
    >
      <p>{t("page.resumeWhat", { summary: schedule.summary })}</p>
      <p>{next ? t("page.resumeNext", { when: next }) : t("page.resumeNextSlot")}</p>
      <p className="text-muted-foreground">{t("page.resumeWhy")}</p>
      {!action && !failed && <p role="status">{t("page.resumePreparing")}</p>}
      {failed && <p role="alert">{t("page.lifecycleError")}</p>}
    </ConfirmDialog>
  );
}

export function LifecycleSection({ snapshot }: { snapshot: Snapshot }) {
  const client = useQueryClient();
  const heading = useId();
  const { session_id: sessionId, name, schedule } = snapshot;
  const retired = snapshot.state === "retired";
  const [dialog, setDialog] = useState<Dialog>(null);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const [pausedHere, setPausedHere] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const opener = useRef<HTMLElement>(null);
  const status = useRef<HTMLParagraphElement>(null);
  const settle = async (next: Snapshot | null) => {
    if (next) client.setQueryData(["heinzel", "session", sessionId], next);
    await Promise.all([
      client.invalidateQueries({ queryKey: ["heinzel", "session", sessionId] }),
      client.invalidateQueries({ queryKey: ["heinzel", "sessions"] }),
    ]);
    status.current?.focus();
  };
  const run = async (call: () => Promise<Snapshot>, after?: () => void) => {
    if (busy) return;
    setBusy(true);
    setFailed(false);
    try {
      const next = await call();
      after?.();
      // A closing dialog hands focus to what changed, not back to a button that may be gone.
      opener.current = status.current;
      setDialog(null);
      await settle(next);
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  };
  const pause = (current: Schedule) =>
    run(
      () =>
        result(
          api.POST("/heinzel/schedule-control", {
            body: {
              session_id: sessionId,
              request_id: crypto.randomUUID(),
              expected_revision: current.revision,
              expected_contract_version: current.contract_version,
              action: "pause",
            },
          }),
        ),
      () => setPausedHere(true),
    );
  const lifecycle = (path: "/heinzel/retire" | "/heinzel/restore") =>
    run(() => result(api.POST(path, { body: { session_id: sessionId, request_id: crypto.randomUUID() } })));
  const open = (which: Dialog) => (event: React.MouseEvent<HTMLButtonElement>) => {
    opener.current = event.currentTarget;
    setDialog(which);
  };
  const line = retired
    ? t("page.lifecycle.retired")
    : uncertain && schedule?.status === "paused"
      ? t("page.resumeUncertain")
      : schedule?.status === "active"
        ? t("page.lifecycle.on")
        : schedule?.status === "paused"
          ? pausedHere
            ? t("page.pausedNow", { name })
            : t("page.lifecycle.paused")
          : t("page.lifecycle.noSchedule");
  return (
    <section aria-labelledby={heading} className="flex flex-col gap-2">
      <h2 id={heading} className="text-base font-semibold">
        {t("page.lifecycle")}
      </h2>
      <p ref={status} tabIndex={-1} role="status" className="text-sm outline-none">
        {line}
      </p>
      <div className="flex flex-wrap gap-2">
        {!retired && schedule?.status === "active" && (
          <Button
            type="button"
            variant="outline"
            className="min-h-11"
            disabled={busy}
            onClick={() => void pause(schedule)}
          >
            <Pause aria-hidden />
            {t("page.pause")}
          </Button>
        )}
        {!retired && !uncertain && schedule?.status === "paused" && (
          <Button type="button" variant="outline" className="min-h-11" disabled={busy} onClick={open("resume")}>
            {pausedHere ? <Undo2 aria-hidden /> : <Play aria-hidden />}
            {pausedHere ? t("page.undo") : t("page.resume")}
          </Button>
        )}
        {retired ? (
          <Button type="button" variant="outline" className="min-h-11" disabled={busy} onClick={open("restore")}>
            <ArchiveRestore aria-hidden />
            {t("page.restore")}
          </Button>
        ) : (
          <Button type="button" variant="outline" className="min-h-11" disabled={busy} onClick={open("retire")}>
            <Archive aria-hidden />
            {t("page.retire")}
          </Button>
        )}
      </div>
      {failed && !dialog && (
        <p role="alert" className="text-sm">
          {t("page.lifecycleError")}
        </p>
      )}
      {dialog === "resume" && schedule && (
        <ResumeDialog
          name={name}
          sessionId={sessionId}
          schedule={schedule}
          opener={opener}
          onDone={(next) => {
            opener.current = status.current;
            setPausedHere(false);
            setUncertain(false);
            setDialog(null);
            void settle(next);
          }}
          onUncertain={() => {
            opener.current = status.current;
            setUncertain(true);
            setDialog(null);
            void settle(null);
          }}
          onClose={() => setDialog(null)}
        />
      )}
      {dialog === "retire" && (
        <ConfirmDialog
          title={t("page.retireTitle", { name })}
          confirm={t("page.retireConfirm", { name })}
          cancel={t("page.keep", { name })}
          opener={opener}
          busy={busy}
          onConfirm={() => void lifecycle("/heinzel/retire")}
          onCancel={() => setDialog(null)}
        >
          <p>{t("page.retireWhat", { name })}</p>
          <p>{t("page.retireKeeps")}</p>
          {failed && <p role="alert">{t("page.lifecycleError")}</p>}
        </ConfirmDialog>
      )}
      {dialog === "restore" && (
        <ConfirmDialog
          title={t("page.restoreTitle", { name })}
          confirm={t("page.restoreConfirm", { name })}
          cancel={t("page.cancel")}
          opener={opener}
          busy={busy}
          onConfirm={() => void lifecycle("/heinzel/restore")}
          onCancel={() => setDialog(null)}
        >
          <p>{t("page.restoreWhat", { name })}</p>
          <p>{t("page.restoreRuns")}</p>
          {failed && <p role="alert">{t("page.lifecycleError")}</p>}
        </ConfirmDialog>
      )}
    </section>
  );
}
