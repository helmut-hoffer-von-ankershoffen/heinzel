// A durable personal session has a different id and service from the legacy runner chat.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { chatKey, sendChat } from "@/api/chat";
import { ApiError, api, problem, type SchemaChatSession, type SchemaSessionSnapshot } from "@/api/client";
import { useDaemonStatus } from "@/api/daemon";
import { AgentProposal } from "@/chat/AgentProposal";
import { AgentProposalSummary } from "@/chat/AgentProposalSummary";
import { AgentSchedules } from "@/chat/AgentSchedules";
import { ConnectedReadReview } from "@/chat/ConnectedReadReview";
import { ConnectedReadSetReview } from "@/chat/ConnectedReadSetReview";
import { ContractHistory } from "@/chat/ContractHistory";
import { ActionCard } from "@/chat/Conversation";
import { useDurableDraft } from "@/chat/durableDraft";
import { ModelPolicyReviewCard, supportedPolicy } from "@/chat/ModelPolicyReviewCard";
import { NotificationPanel } from "@/chat/NotificationPanel";
import { SkillDraftPanel } from "@/chat/SkillDraftPanel";
import { ToolEvents } from "@/chat/ToolEvents";
import { UncertainTurnRecovery } from "@/chat/UncertainTurnRecovery";
import { Button } from "@/components/ui/button";
import { ProfileEditor } from "@/heinzel/PersonalHeinzels";
import { t } from "@/i18n";
import { PersonalArtifactLinks } from "@/pages/PersonalArtifacts";

async function result<T>(call: Promise<{ data?: T; error?: unknown; response: Response }>): Promise<T> {
  const { data, error, response } = await call;
  if (response.ok && data !== undefined) return data;
  throw problem(response.status, error);
}

const DAEMON_TICK_CLOCK_SKEW_MS = 60_000;

function inputComparison(status: "first" | "unchanged" | "changed"): string {
  if (status === "first") return "first recorded result";
  return status === "unchanged"
    ? "same exact result as the previous read under this approval"
    : "different exact result from the previous read under this approval";
}

function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.entries(value)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([key, item]) => `${JSON.stringify(key)}:${canonical(item)}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}

function requirePersonalChat(chat: SchemaChatSession, sessionId: string, chatId?: string): SchemaChatSession {
  if (chat.personal_session_id !== sessionId || (chatId !== undefined && chat.id !== chatId)) {
    throw new Error("This conversation does not belong to the selected Heinzel.");
  }
  return chat;
}

function rejectedBeforeAcceptance(reason: unknown): boolean {
  return (
    reason instanceof ApiError &&
    reason.status === 400 &&
    reason.message === "This personal agent cannot run until its runner isolation and grants are verified"
  );
}

export function DurableSession({ sessionId, onClose }: { sessionId: string; onClose: () => void }) {
  const client = useQueryClient();
  const [params, setParams] = useSearchParams();
  const {
    state: composer,
    update: updateComposer,
    storageError,
    recoveryBlocked,
    resolveRecovery,
  } = useDurableDraft(sessionId);
  const { text, pending, agentPending, checkInRequest } = composer;
  const [error, setError] = useState(false);
  const [profileLocked, setProfileLocked] = useState(false);
  const [showProposal, setShowProposal] = useState(false);
  const [busy, setBusy] = useState(false);
  const [scheduleBusy, setScheduleBusy] = useState(false);
  const [scheduleError, setScheduleError] = useState(false);
  const [scheduleErrorMessage, setScheduleErrorMessage] = useState<string | null>(null);
  const [intentNotice, setIntentNotice] = useState(false);
  const [activityLimit, setActivityLimit] = useState(20);
  const [historyCursors, setHistoryCursors] = useState<Array<string | null>>([null]);
  const historyCursor = historyCursors.at(-1) ?? null;
  const clearScheduleError = () => {
    setScheduleError(false);
    setScheduleErrorMessage(null);
  };
  const visibleScheduleError = (reason: unknown): string | null => {
    const message = reason instanceof Error ? reason.message : "";
    return message.startsWith("Check-ins are blocked because the daemon unit version") ? message : null;
  };
  const controlRequest = useRef<string | null>(params.get("approval-request"));
  const composerInput = useRef<HTMLTextAreaElement>(null);
  const running = useRef(false);
  const query = useQuery({
    queryKey: ["heinzel", "session", sessionId, ...(historyCursor ? [historyCursor] : [])],
    queryFn: async () => {
      const snapshot = await result(
        api.GET("/heinzel/session", {
          params: { query: { session_id: sessionId, cursor: historyCursor ?? undefined } },
        }),
      );
      if (snapshot.session_id !== sessionId) throw new Error("Unexpected personal session");
      return snapshot;
    },
    retry: false,
    placeholderData: (previous) => previous,
  });
  const profileProposal = useQuery({
    queryKey: ["heinzel", "profile-proposal", sessionId],
    queryFn: async () => {
      const suggestion = await result(
        api.GET("/heinzel/latest-profile-proposal", {
          params: { query: { session_id: sessionId } },
        }),
      );
      if (suggestion && (suggestion.session_id !== sessionId || suggestion.status !== "proposed"))
        throw new Error("Unexpected profile proposal");
      return suggestion;
    },
    retry: false,
  });
  const activity = useQuery({
    queryKey: ["heinzel", "tool-activity", sessionId, activityLimit],
    queryFn: async () => {
      const page = await result(
        api.GET("/heinzel/tool-activity", { params: { query: { session_id: sessionId, limit: activityLimit } } }),
      );
      if (page.session_id !== sessionId) throw new Error("Unexpected activity session");
      return page;
    },
    retry: false,
  });
  const inputSnapshots = useQuery({
    queryKey: ["heinzel", "input-snapshot-history", sessionId],
    queryFn: async () => {
      const page = await result(
        api.GET("/heinzel/input-snapshot-history", { params: { query: { session_id: sessionId, limit: 20 } } }),
      );
      if (
        page.session_id !== sessionId ||
        page.items.length > 20 ||
        page.items.some(
          (item, index) =>
            !Number.isInteger(item.seq) ||
            item.seq <= (index === 0 ? 0 : (page.items[index - 1]?.seq ?? 0)) ||
            !/^interactive-[0-9a-f]{64}$/.test(item.turn_id) ||
            !["first", "unchanged", "changed"].includes(item.status),
        )
      )
        throw new Error("Unexpected input history");
      return page;
    },
    retry: false,
  });
  const daemonStatus = useDaemonStatus(query.data?.schedule?.status === "active");
  const approvalChatId = params.get("approval-chat");
  const agentChatId = params.get("agent-chat");
  const rememberChat = (chat: SchemaChatSession, expectedId?: string) => {
    requirePersonalChat(chat, sessionId, expectedId);
    client.setQueryData(chatKey(chat.id), chat);
  };
  const agentChatQuery = useQuery({
    queryKey: chatKey(agentChatId ?? ""),
    queryFn: async () =>
      requirePersonalChat(
        await result(api.GET("/system/runner/chat/show", { params: { query: { session: agentChatId ?? "" } } })),
        sessionId,
        agentChatId ?? "",
      ),
    enabled: agentChatId !== null,
    retry: false,
  });
  const agentChat =
    agentChatQuery.data?.personal_session_id === sessionId && agentChatQuery.data.id === agentChatId
      ? agentChatQuery.data
      : undefined;
  const agentChatUnavailable = agentChatId !== null && !agentChat;
  const approvalQuery = useQuery({
    queryKey: chatKey(approvalChatId ?? ""),
    queryFn: async () =>
      requirePersonalChat(
        await result(api.GET("/system/runner/chat/show", { params: { query: { session: approvalChatId ?? "" } } })),
        sessionId,
        approvalChatId ?? "",
      ),
    enabled: approvalChatId !== null,
    retry: false,
  });
  const approvalChat =
    approvalQuery.data?.personal_session_id === sessionId && approvalQuery.data.id === approvalChatId
      ? approvalQuery.data
      : undefined;
  const activeDaemon = query.data?.schedule?.status === "active" ? daemonStatus.data : undefined;
  const daemonStatusUpdatedAt = daemonStatus.dataUpdatedAt;
  const daemonTickAgeMs = activeDaemon?.last_tick ? Date.now() - Date.parse(activeDaemon.last_tick) : null;
  const staleDaemonTick =
    daemonStatusUpdatedAt > 0 &&
    activeDaemon?.installed === true &&
    (daemonTickAgeMs === null ||
      !Number.isFinite(daemonTickAgeMs) ||
      daemonTickAgeMs < -DAEMON_TICK_CLOCK_SKEW_MS ||
      (activeDaemon.interval_minutes !== null && daemonTickAgeMs > activeDaemon.interval_minutes * 2 * 60_000));
  const daemonReadinessMessage = activeDaemon
    ? !activeDaemon.installed
      ? t("durable.daemonAbsent")
      : activeDaemon.paused
        ? t("durable.daemonPaused")
        : !activeDaemon.loaded ||
            !activeDaemon.version ||
            !activeDaemon.installed_version ||
            activeDaemon.version_mismatch
          ? t("durable.daemonVersionNeedsCheck")
          : activeDaemon.interval_minutes === null || activeDaemon.interval_minutes > 5
            ? t("durable.daemonCadence")
            : activeDaemon.failing.length > 0 || staleDaemonTick
              ? t("durable.daemonNeedsCheck")
              : null
    : null;
  const agentActions = (agentChat?.actions ?? []).filter(
    (action) =>
      action.module === "heinzel" &&
      action.operation === "propose-check-in" &&
      action.arguments?.session_id === sessionId,
  );
  const pendingAgentActions = agentActions.filter((action) => action.status === "pending");
  const runningAgentActions = agentActions.filter((action) => action.status === "running");
  const uncertainAgentActions = agentActions.filter((action) => action.status === "uncertain");
  const approvalActions = (approvalChat?.actions ?? []).filter(
    (action) =>
      action.module === "heinzel" &&
      action.operation === "schedule-control" &&
      action.arguments?.session_id === sessionId &&
      (action.arguments?.action === "activate" ||
        action.arguments?.action === "pause" ||
        action.arguments?.action === "resume") &&
      !Object.hasOwn(action.arguments ?? {}, "approval_proof"),
  );
  const matchesCurrentSchedule = (action: (typeof approvalActions)[number]) => {
    const schedule = query.data?.schedule;
    const args = action.arguments;
    return (
      schedule !== null &&
      schedule !== undefined &&
      args !== undefined &&
      schedule.revision === args.expected_revision &&
      schedule.contract_version === args.expected_contract_version &&
      schedule.preview_hash === args.expected_preview_hash &&
      schedule.status === (args.action === "activate" ? "proposed" : args.action === "resume" ? "paused" : "active")
    );
  };
  const pendingApprovalActions = approvalActions.filter(
    (action) => action.status === "pending" && matchesCurrentSchedule(action),
  );
  const staleApprovalActions = approvalActions.filter(
    (action) => action.status === "pending" && query.data !== undefined && !matchesCurrentSchedule(action),
  );
  const runningApprovalActions = approvalActions.filter((action) => action.status === "running");
  const uncertainApprovalActions = approvalActions.filter((action) => action.status === "uncertain");
  const scheduleApprovalNeedsAttention =
    approvalChatId !== null ||
    pendingApprovalActions.length > 0 ||
    staleApprovalActions.length > 0 ||
    runningApprovalActions.length > 0;
  useEffect(() => {
    if (
      approvalChatId === null ||
      approvalQuery.isPending ||
      approvalQuery.isError ||
      approvalActions.length === 0 ||
      !approvalActions.every((action) => ["done", "failed", "declined"].includes(action.status))
    )
      return;
    setParams((current) => {
      const next = new URLSearchParams(current);
      next.delete("approval-chat");
      return next;
    });
  }, [approvalActions, approvalChatId, approvalQuery.isError, approvalQuery.isPending, setParams]);
  const send = async () => {
    if (agentChatUnavailable) return;
    if (recoveryBlocked || storageError) {
      setError(true);
      return;
    }
    if (
      running.current ||
      pending?.outcome === "subscription_usage_limit" ||
      (!pending && !agentPending && !text.trim())
    )
      return;
    const requestedMessage = agentPending?.message ?? pending?.message ?? text;
    if (!pending && agentPending !== null && agentChatId === null) {
      running.current = true;
      setBusy(true);
      setError(false);
      setIntentNotice(false);
      let openedChatId: string | null = agentChatId;
      let requestId = agentPending?.request_id ?? null;
      try {
        const stableRequestId = agentPending?.request_id ?? crypto.randomUUID();
        requestId = stableRequestId;
        if (!updateComposer({ agentPending: { request_id: stableRequestId, message: requestedMessage } })) {
          throw new Error("The message could not be preserved in this tab.");
        }
        setParams(
          (current) => {
            const next = new URLSearchParams(current);
            next.set("agent-open-request", stableRequestId);
            return next;
          },
          { replace: true },
        );
        const opened = await result(
          api.POST("/system/runner/chat/open-check-in", {
            params: {
              query: {
                session_id: sessionId,
                request_id: stableRequestId,
                timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
              },
            },
          }),
        );
        openedChatId = opened.id;
        rememberChat(opened);
        setParams(
          (current) => {
            const next = new URLSearchParams(current);
            next.delete("agent-open-request");
            next.set("agent-chat", opened.id);
            return next;
          },
          { replace: true },
        );
        const answered = await sendChat(opened.id, requestedMessage, () => {}, {}, stableRequestId);
        rememberChat(answered, opened.id);
        const last = answered.messages?.at(-1);
        if (last?.role === "assistant" && last.status !== "completed") {
          if (last.status !== "pending") updateComposer({ agentPending: null });
          setScheduleError(true);
          setScheduleErrorMessage(`${last.detail} ${t("durable.agentUnavailable")}`.trim());
        } else {
          updateComposer({ agentPending: null, text: "" });
          setIntentNotice(true);
        }
      } catch (reason) {
        if (rejectedBeforeAcceptance(reason)) updateComposer({ agentPending: null });
        setError(true);
        setScheduleError(true);
        setScheduleErrorMessage(t("durable.agentUnavailable"));
        if (openedChatId !== null) {
          try {
            const current = await result(
              api.GET("/system/runner/chat/show", { params: { query: { session: openedChatId } } }),
            );
            rememberChat(current, openedChatId);
            const last = current.messages?.at(-1);
            if (last?.role === "assistant" && last.request_id === requestId && last.status !== "pending") {
              updateComposer({ agentPending: null, text: "" });
            }
          } catch {
            // The stable chat link remains available for a later inspection.
          }
        }
      } finally {
        running.current = false;
        setBusy(false);
      }
      return;
    }
    if (agentChatId !== null && agentPending !== null) {
      running.current = true;
      setBusy(true);
      setError(false);
      let requestId = agentPending?.request_id ?? null;
      try {
        const request = agentPending ?? { request_id: crypto.randomUUID(), message: requestedMessage };
        requestId = request.request_id;
        if (!agentPending && !updateComposer({ agentPending: request })) {
          throw new Error("The message could not be preserved in this tab.");
        }
        const answered = await sendChat(agentChatId, request.message, () => {}, {}, request.request_id);
        rememberChat(answered, agentChatId);
        const last = answered.messages?.at(-1);
        if (last?.role === "assistant" && last.status !== "completed") {
          if (last.status !== "pending") updateComposer({ agentPending: null });
          setScheduleError(true);
          setScheduleErrorMessage(`${last.detail} ${t("durable.agentUnavailable")}`.trim());
        } else {
          updateComposer({ agentPending: null, text: "" });
        }
      } catch (reason) {
        if (rejectedBeforeAcceptance(reason)) updateComposer({ agentPending: null });
        setError(true);
        setScheduleError(true);
        setScheduleErrorMessage(t("durable.agentUnavailable"));
        try {
          const current = await result(
            api.GET("/system/runner/chat/show", { params: { query: { session: agentChatId } } }),
          );
          rememberChat(current, agentChatId);
          const last = current.messages?.at(-1);
          if (last?.role === "assistant" && last.request_id === requestId && last.status !== "pending") {
            updateComposer({ agentPending: null, text: "" });
          }
        } catch {
          // Keep the request id; the next click retries the same message without replaying it.
        }
      } finally {
        running.current = false;
        setBusy(false);
      }
      return;
    }
    running.current = true;
    const request = pending ?? { request_id: crypto.randomUUID(), message: text };
    if (!pending && !updateComposer({ pending: request })) {
      setError(true);
      running.current = false;
      return;
    }
    setBusy(true);
    setError(false);
    let acceptedConfirmed = request.accepted === true;
    try {
      if (!request.accepted) {
        const accepted = await result(
          api.POST("/heinzel/chat", {
            body: {
              request_id: request.request_id,
              message: request.message,
              session_id: sessionId,
              when_busy: "follow_up",
            },
          }),
        );
        if (accepted.session_id !== sessionId || accepted.request_id !== request.request_id)
          throw new Error("Invalid acknowledgement");
        acceptedConfirmed = true;
        if (!updateComposer({ pending: { ...request, accepted: true } }))
          throw new Error("The accepted request could not be preserved in this tab.");
      }
      const answered = await result(
        api.POST("/heinzel/answer-chat", { body: { session_id: sessionId, request_id: request.request_id } }),
      );
      if (
        answered.session_id !== sessionId ||
        answered.request_id !== request.request_id ||
        answered.message.role !== "assistant"
      )
        throw new Error("Invalid answer acknowledgement");
      updateComposer({ pending: null, text: "" });
      await client.invalidateQueries({ queryKey: ["heinzel", "session", sessionId] });
      await client.invalidateQueries({ queryKey: ["heinzel", "tool-activity", sessionId] });
      await client.invalidateQueries({ queryKey: ["heinzel", "input-snapshot-history", sessionId] });
      await client.invalidateQueries({ queryKey: ["heinzel", "notification-proposals", sessionId] });
      await client.invalidateQueries({ queryKey: ["heinzel", "agent-proposal", sessionId] });
      await client.invalidateQueries({ queryKey: ["heinzel", "profile-proposal", sessionId] });
      await client.invalidateQueries({ queryKey: ["heinzel", "latest-schedule-suggestion", sessionId] });
      await client.invalidateQueries({ queryKey: ["heinzel", "latest-source-suggestion", sessionId] });
      await client.invalidateQueries({ queryKey: ["heinzel", "connected-read-set", sessionId] });
    } catch (reason) {
      if (acceptedConfirmed && reason instanceof ApiError && reason.code === "subscription_usage_limit") {
        updateComposer({ pending: { ...request, accepted: true, outcome: "subscription_usage_limit" } });
      }
      setError(true);
      await client.invalidateQueries({ queryKey: ["heinzel", "session", sessionId] });
    } finally {
      running.current = false;
      setBusy(false);
    }
  };

  const proposeCheckIn = async (): Promise<SchemaSessionSnapshot | null> => {
    if (scheduleBusy || !query.data || recoveryBlocked) return null;
    const requestId = checkInRequest ?? crypto.randomUUID();
    if (!checkInRequest && !updateComposer({ checkInRequest: requestId })) {
      setScheduleError(true);
      setScheduleErrorMessage(t("durable.preservationFailed"));
      return null;
    }
    setScheduleBusy(true);
    clearScheduleError();
    try {
      const proposed = await result(
        api.POST("/heinzel/propose-check-in", {
          body: {
            session_id: sessionId,
            request_id: requestId,
            timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
          },
        }),
      );
      updateComposer({ checkInRequest: null });
      client.setQueryData(["heinzel", "session", sessionId], proposed);
      await client.invalidateQueries({ queryKey: ["heinzel", "session", sessionId] });
      return proposed;
    } catch (reason) {
      setScheduleError(true);
      setScheduleErrorMessage(visibleScheduleError(reason));
      return null;
    } finally {
      setScheduleBusy(false);
    }
  };

  const reviewSchedule = async (scheduleOverride?: SchemaSessionSnapshot["schedule"]) => {
    const schedule = scheduleOverride ?? query.data?.schedule;
    if (
      scheduleBusy ||
      !schedule ||
      (schedule.status !== "proposed" && schedule.status !== "paused" && schedule.status !== "active")
    )
      return;
    controlRequest.current ??= params.get("approval-request") ?? crypto.randomUUID();
    const requestId = controlRequest.current;
    setParams(
      (current) => {
        const next = new URLSearchParams(current);
        next.set("approval-request", requestId);
        return next;
      },
      { replace: true },
    );
    setScheduleBusy(true);
    clearScheduleError();
    try {
      const chat = await result(
        api.POST("/system/runner/chat/propose-schedule-control", {
          params: {
            query: {
              durable_session: sessionId,
              request_id: requestId,
              expected_revision: schedule.revision,
              action: schedule.status === "proposed" ? "activate" : schedule.status === "paused" ? "resume" : "pause",
              expected_contract_version: schedule.contract_version,
              expected_preview_hash: schedule.preview_hash,
            },
          },
        }),
      );
      controlRequest.current = null;
      rememberChat(chat);
      setParams(
        (current) => {
          const next = new URLSearchParams(current);
          next.delete("approval-request");
          next.set("approval-chat", chat.id);
          return next;
        },
        { replace: true },
      );
    } catch (reason) {
      setScheduleError(true);
      setScheduleErrorMessage(visibleScheduleError(reason));
    } finally {
      setScheduleBusy(false);
    }
  };

  const decideSchedule = async (action: string, confirm: boolean) => {
    if (!approvalChat || scheduleBusy) return;
    setScheduleBusy(true);
    clearScheduleError();
    try {
      const updated = await result(
        confirm
          ? api.POST("/system/runner/chat/confirm", { params: { query: { session: approvalChat.id, action } } })
          : api.POST("/system/runner/chat/decline", { params: { query: { session: approvalChat.id, action } } }),
      );
      rememberChat(updated, approvalChat.id);
      const decided = updated.actions?.find((item) => item.id === action)?.status;
      if (decided === "done" || decided === "failed" || decided === "declined") forgetApprovalChat();
    } catch (reason) {
      setScheduleError(true);
      setScheduleErrorMessage(visibleScheduleError(reason));
      try {
        const current = await result(
          api.GET("/system/runner/chat/show", { params: { query: { session: approvalChat.id } } }),
        );
        rememberChat(current, approvalChat.id);
        const decided = current.actions?.find((item) => item.id === action)?.status;
        if (decided === "done" || decided === "failed" || decided === "declined") forgetApprovalChat();
      } catch {
        // The stored proposal remains safe; a reload can inspect it from the chat list.
      }
    } finally {
      await client.invalidateQueries({ queryKey: ["heinzel", "session", sessionId] });
      setScheduleBusy(false);
    }
  };

  const recoverAgentProposal = async (action: string) => {
    if (!agentChat || busy) return;
    setBusy(true);
    try {
      const updated = await result(
        api.POST("/system/runner/chat/recover", { params: { query: { session: agentChat.id, action } } }),
      );
      rememberChat(updated, agentChat.id);
      await client.invalidateQueries({ queryKey: ["heinzel", "session", sessionId] });
    } catch (reason) {
      setScheduleError(true);
      setScheduleErrorMessage(reason instanceof Error ? reason.message : null);
    } finally {
      setBusy(false);
    }
  };

  const authorizeScheduleForMcp = async (action: string) => {
    if (!approvalChat || scheduleBusy) return;
    setScheduleBusy(true);
    clearScheduleError();
    try {
      const updated = await result(
        api.POST("/system/runner/chat/authorize-for-mcp", {
          params: { query: { session: approvalChat.id, action } },
        }),
      );
      rememberChat(updated, approvalChat.id);
    } catch (reason) {
      setScheduleError(true);
      setScheduleErrorMessage(visibleScheduleError(reason));
    } finally {
      setScheduleBusy(false);
    }
  };

  const forgetApprovalChat = () => {
    setParams((current) => {
      const next = new URLSearchParams(current);
      next.delete("approval-chat");
      return next;
    });
  };

  const recoverSchedule = async (action: string) => {
    if (!approvalChat || scheduleBusy) return;
    setScheduleBusy(true);
    clearScheduleError();
    try {
      const updated = await result(
        api.POST("/system/runner/chat/recover", {
          params: { query: { session: approvalChat.id, action } },
        }),
      );
      rememberChat(updated, approvalChat.id);
      if (updated.actions?.find((item) => item.id === action)?.status === "done") forgetApprovalChat();
      await client.invalidateQueries({ queryKey: ["heinzel", "session", sessionId] });
    } catch (reason) {
      setScheduleError(true);
      setScheduleErrorMessage(visibleScheduleError(reason));
      try {
        const current = await result(
          api.GET("/system/runner/chat/show", { params: { query: { session: approvalChat.id } } }),
        );
        rememberChat(current, approvalChat.id);
      } catch {
        // Keep the recovery link; a later check still cannot rerun the action.
      }
    } finally {
      setScheduleBusy(false);
    }
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex h-12 shrink-0 items-center justify-between gap-2 border-b border-border px-3">
        <h2 className="min-w-0 truncate font-medium">{query.data?.name ?? t("durable.title")}</h2>
        <Button type="button" variant="ghost" size="sm" disabled={busy || profileLocked} onClick={onClose}>
          {t("durable.close")}
        </Button>
      </div>
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3">
        {query.isPending && <p role="status">{t("state.loading")}</p>}
        {query.isError && <p role="alert">{t("durable.unavailable")}</p>}
        {query.data && (
          <details className="rounded-lg border border-border p-3">
            <summary className="cursor-pointer text-sm font-medium">
              {t("personal.customize")}
              {profileProposal.data?.session_id === sessionId && " · suggested look"}
            </summary>
            <ProfileEditor
              initial={query.data}
              onLock={setProfileLocked}
              inChat
              suggestion={profileProposal.data ?? null}
            />
          </details>
        )}
        {query.data && (
          <details
            className="rounded-lg border border-border p-3"
            onToggle={(event) => {
              if (event.currentTarget.open) setShowProposal(true);
            }}
          >
            <summary className="cursor-pointer text-sm font-medium">Scheduled agent terms</summary>
            {showProposal && <AgentProposal sessionId={sessionId} />}
          </details>
        )}
        {query.data?.session_id === sessionId && (
          <PersonalArtifactLinks key={`artifacts:${sessionId}`} sessionId={sessionId} compact />
        )}
        {query.data?.session_id === sessionId && <AgentProposalSummary sessionId={sessionId} />}
        {query.data?.session_id === sessionId && (
          <NotificationPanel key={`notification:${sessionId}`} sessionId={sessionId} />
        )}
        {query.data?.session_id === sessionId && (
          <>
            <ConnectedReadReview key={`connected:${sessionId}`} sessionId={sessionId} />
            <ConnectedReadSetReview
              key={`connected-set:${sessionId}`}
              sessionId={sessionId}
              onAsk={() => composerInput.current?.focus()}
            />
          </>
        )}
        {query.data?.session_id === sessionId && <ContractHistory contracts={query.data.contracts ?? []} />}
        {query.data?.session_id === sessionId && <SkillDraftPanel key={`skills:${sessionId}`} sessionId={sessionId} />}
        {(historyCursors.length > 1 || query.data?.has_more) && (
          <nav aria-label="Conversation history" className="space-y-2 rounded border border-border p-2 text-sm">
            {query.isFetching || query.isPlaceholderData ? (
              <p role="status">Loading this message page…</p>
            ) : query.isError ? (
              <div className="space-y-2" role="alert">
                <p>This message page could not be loaded. You can retry or return to an earlier page.</p>
                <Button type="button" size="sm" variant="outline" onClick={() => void query.refetch()}>
                  Retry message page
                </Button>
              </div>
            ) : query.data ? (
              <p>
                {query.data.has_more
                  ? "More messages follow this page, including any newer queued messages."
                  : "You are viewing the latest message page."}
              </p>
            ) : null}
            <div className="flex flex-wrap gap-2">
              {historyCursors.length > 1 && (
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={query.isFetching || busy || profileLocked}
                  onClick={() => setHistoryCursors((cursors) => cursors.slice(0, -1))}
                >
                  Earlier messages
                </Button>
              )}
              {query.data?.has_more && typeof query.data.next_cursor === "string" && (
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={
                    query.isFetching || busy || profileLocked || historyCursors.includes(query.data.next_cursor)
                  }
                  onClick={() => {
                    const next = query.data?.next_cursor;
                    if (typeof next === "string" && next && !historyCursors.includes(next))
                      setHistoryCursors((cursors) => [...cursors, next]);
                  }}
                >
                  Newer messages
                </Button>
              )}
            </div>
          </nav>
        )}
        <AgentSchedules key={sessionId} sessionId={sessionId} />
        {query.data?.messages?.map((message) => {
          const turnItems =
            message.role === "assistant" && message.turn_id
              ? (activity.data?.items ?? []).filter((item) => item.turn_id === message.turn_id)
              : [];
          return (
            <div key={`${message.seq}-${message.id}`} className="space-y-1 text-sm">
              <p className="whitespace-pre-wrap break-words">
                <strong>
                  {message.role === "tool" ? "Tool activity" : message.origin === "daemon" ? "Daemon" : message.role}:
                </strong>{" "}
                {message.role === "tool" ? "A tool event was recorded." : message.text}
                {message.status && message.status !== "completed" && <span> ({message.status})</span>}
                {message.billing === "plan" && (
                  <span className="block text-xs text-muted-foreground">Subscription usage · cost unknown</span>
                )}
                {message.billing === "api" && message.cost_usd != null && (
                  <span className="block text-xs text-muted-foreground">API cost: ${message.cost_usd.toFixed(4)}</span>
                )}
              </p>
              {turnItems.length > 0 && <ToolEvents items={turnItems} label="Tool activity for this reply" />}
              {message.role === "assistant" &&
                message.turn_id &&
                inputSnapshots.data?.items
                  .filter((item) => item.turn_id === message.turn_id)
                  .map((item) => (
                    <p key={item.seq} className="text-xs text-muted-foreground">
                      Approved read: {inputComparison(item.status)}.
                    </p>
                  ))}
              {message.role === "user" && message.origin === "daemon" && (
                <DaemonTurnInspection
                  sessionId={sessionId}
                  requestId={message.id}
                  onResolved={() => {
                    void client.invalidateQueries({ queryKey: ["heinzel", "session", sessionId] });
                  }}
                />
              )}
            </div>
          );
        })}
        {activity.isError && (
          <p role="status" className="text-sm">
            Recorded tool activity is unavailable.
          </p>
        )}
        {inputSnapshots.isError && (
          <p role="status" className="text-sm">
            Input comparison history is unavailable.
          </p>
        )}
        {!!inputSnapshots.data?.items.length &&
          (() => {
            const visibleTurns = new Set(
              (query.data?.messages ?? [])
                .filter((message) => message.role === "assistant")
                .map((message) => message.turn_id),
            );
            const unmatched = inputSnapshots.data.items.filter((item) => !visibleTurns.has(item.turn_id));
            return (
              <section aria-label="Recent input comparisons" className="text-xs text-muted-foreground">
                <p>
                  Showing up to 20 recent approved read comparisons. Exact results may omit source events; this is not
                  event deduplication or complete history.
                </p>
                {unmatched.map((item) => (
                  <p key={item.seq}>Read without a visible reply: {inputComparison(item.status)}.</p>
                ))}
              </section>
            );
          })()}
        {activity.data?.session_id === sessionId &&
          activity.data.items.length > 0 &&
          (() => {
            const visibleTurns = new Set(
              (query.data?.messages ?? [])
                .filter((message) => message.role === "assistant")
                .map((message) => message.turn_id),
            );
            const unmatched = activity.data.items.filter((item) => !visibleTurns.has(item.turn_id));
            return (
              <section
                aria-label="Recorded tool activity"
                className="space-y-2 rounded border border-border p-3 text-sm"
              >
                <h3 className="font-medium">Recorded tool activity</h3>
                <p>Showing up to the latest {activityLimit} recorded events. Earlier activity may be omitted.</p>
                {[...new Set(unmatched.map((item) => item.turn_id))].map((turn) => (
                  <ToolEvents
                    key={turn}
                    items={unmatched.filter((item) => item.turn_id === turn)}
                    label="Activity for a turn without a visible reply"
                  />
                ))}
                {activityLimit < 100 && activity.data.items.length === activityLimit && (
                  <Button type="button" size="sm" variant="outline" onClick={() => setActivityLimit(100)}>
                    Show more recorded activity
                  </Button>
                )}
                {activityLimit === 100 && <p>Events older than the latest 100 are not available here yet.</p>}
              </section>
            );
          })()}
        {(query.data?.pending_actions ?? []).map((action) => (
          <section
            key={action.id}
            aria-label="Action awaiting review"
            className="space-y-1 rounded border border-border p-3 text-sm"
          >
            <h3 className="font-medium">{action.preview?.title || "Action proposal"}</h3>
            <p>Status: {action.status === "pending" ? "Waiting for approval" : action.status}</p>
            {action.preview?.target && <p>Target: {action.preview.target}</p>}
            {action.preview?.before && <p className="whitespace-pre-wrap">Before: {action.preview.before}</p>}
            {action.preview?.after && <p className="whitespace-pre-wrap">Draft: {action.preview.after}</p>}
            {action.preview?.diff && <p className="whitespace-pre-wrap">Changes: {action.preview.diff}</p>}
            <p>Approval for this durable action is not available here yet.</p>
          </section>
        ))}
        {agentChatId && agentChatQuery.isPending && <p role="status">{t("durable.agentLoading")}</p>}
        {agentChatId && !agentChatQuery.isPending && agentChatUnavailable && (
          <p role="alert">{t("durable.agentUnavailable")}</p>
        )}
        {agentChat?.messages?.map((message) => (
          <p key={`${message.role}-${message.request_id ?? message.at}`} className="whitespace-pre-wrap text-sm">
            <strong>{message.role}:</strong> {message.text || message.detail}
            {message.status && message.status !== "completed" && <span> ({message.status})</span>}
            {message.tools && Object.keys(message.tools).length > 0 && (
              <span className="block">
                Tools used:{" "}
                {Object.entries(message.tools)
                  .map(([name, count]) => `${name} (${count})`)
                  .join(", ")}
              </span>
            )}
          </p>
        ))}
        {[agentChat, approvalChat]
          .filter((chat, index, all) => chat && all.findIndex((item) => item?.id === chat.id) === index)
          .flatMap((chat) =>
            (chat?.actions ?? []).map((action) => (
              <ModelPolicyReviewCard
                key={`${chat?.id}-${action.id}`}
                action={action}
                sessionId={sessionId}
                busy={busy}
                onConfirm={() => {
                  if (!chat || busy) return;
                  void (async () => {
                    setBusy(true);
                    setError(false);
                    try {
                      const args = action.arguments;
                      if (!args || typeof args.request_id !== "string" || !supportedPolicy(args.review))
                        throw new Error("This model policy cannot be confirmed.");
                      const current = await result(
                        api.GET("/heinzel/model-policy", {
                          params: { query: { session_id: sessionId, request_id: args.request_id } },
                        }),
                      );
                      if (
                        current.session_id !== sessionId ||
                        current.status !== "proposed" ||
                        canonical(current.proposal) !== canonical(args.review) ||
                        !supportedPolicy(current.proposal)
                      )
                        throw new Error("The model policy changed; review it again.");
                      const updated = await result(
                        api.POST("/system/runner/chat/confirm", {
                          params: { query: { session: chat.id, action: action.id } },
                        }),
                      );
                      rememberChat(updated, chat.id);
                    } catch {
                      setError(true);
                      try {
                        const updated = await result(
                          api.GET("/system/runner/chat/show", {
                            params: { query: { session: chat.id } },
                          }),
                        );
                        rememberChat(updated, chat.id);
                      } catch {
                        // Keep the action available for an explicit later inspection, without retrying confirmation.
                      }
                    } finally {
                      setBusy(false);
                    }
                  })();
                }}
              />
            )),
          )}
        {pendingAgentActions.length > 0 && (
          <ol className="space-y-2">
            {pendingAgentActions.map((action) => (
              <ActionCard
                key={`${agentChat?.id}-${action.id}`}
                action={action}
                busy={busy}
                proposalTitle={t("durable.scheduleActionTitle")}
                onDecide={(confirm) => {
                  if (!agentChat) return;
                  void (async () => {
                    setBusy(true);
                    try {
                      const updated = await result(
                        confirm
                          ? api.POST("/system/runner/chat/confirm", {
                              params: { query: { session: agentChat.id, action: action.id } },
                            })
                          : api.POST("/system/runner/chat/decline", {
                              params: { query: { session: agentChat.id, action: action.id } },
                            }),
                      );
                      rememberChat(updated, agentChat.id);
                      await client.invalidateQueries({ queryKey: ["heinzel", "session", sessionId] });
                    } catch (reason) {
                      setScheduleError(true);
                      setScheduleErrorMessage(reason instanceof Error ? reason.message : null);
                      try {
                        const current = await result(
                          api.GET("/system/runner/chat/show", { params: { query: { session: agentChat.id } } }),
                        );
                        rememberChat(current, agentChat.id);
                        await client.invalidateQueries({ queryKey: ["heinzel", "session", sessionId] });
                      } catch {
                        // Keep the recovery link; a later reload reads the same action without replaying it.
                      }
                    } finally {
                      setBusy(false);
                    }
                  })();
                }}
              />
            ))}
          </ol>
        )}
        {runningAgentActions.map((action) => (
          <div key={`agent-${action.id}`} className="space-y-2 rounded border border-warn p-3 text-sm" role="status">
            <p>{t("durable.proposalInterrupted")}</p>
            <Button type="button" size="sm" disabled={busy} onClick={() => void recoverAgentProposal(action.id)}>
              {t("durable.proposalCheckOutcome")}
            </Button>
          </div>
        ))}
        {uncertainAgentActions.map((action) => (
          <p key={`agent-${action.id}`} className="text-sm text-bad" role="alert">
            {action.result || t("durable.proposalOutcomeUnknown")}
          </p>
        ))}
        {query.data && (
          <section aria-label={t("durable.schedule")} className="space-y-2 rounded-lg border border-border p-3">
            <h3 className="font-medium">{t("durable.schedule")}</h3>
            {query.data.schedule ? (
              <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
                <dt>{t("durable.scheduleStatus")}</dt>
                <dd>{query.data.schedule.status}</dd>
                <dt>{t("durable.scheduleCadence")}</dt>
                <dd>
                  {query.data.schedule.cron} · {query.data.schedule.timezone}
                </dd>
                <dt>{t("durable.schedulePreview")}</dt>
                <dd className="break-all font-mono text-xs">{query.data.schedule.preview_hash}</dd>
              </dl>
            ) : (
              <p className="text-sm text-muted-foreground">{t("durable.scheduleEmpty")}</p>
            )}
            {daemonReadinessMessage && (
              <p className="text-sm text-warn" role="status">
                {daemonReadinessMessage}{" "}
                <Link className="underline" to="/?settings=daemon">
                  {t("durable.daemonSettings")}
                </Link>
              </p>
            )}
            {query.data.schedule?.status === "active" && daemonStatus.isError && (
              <p className="text-sm text-warn" role="status">
                {t("durable.daemonStatusUnavailable")}{" "}
                <Link className="underline" to="/?settings=daemon">
                  {t("durable.daemonSettings")}
                </Link>
              </p>
            )}
            {query.data.schedule && !scheduleApprovalNeedsAttention && uncertainApprovalActions.length === 0 ? (
              <Button type="button" size="sm" disabled={scheduleBusy} onClick={() => void reviewSchedule()}>
                {scheduleBusy
                  ? t("durable.scheduleWorking")
                  : query.data.schedule.status === "active"
                    ? t("durable.schedulePauseReview")
                    : query.data.schedule.status === "paused"
                      ? t("durable.scheduleResumeReview")
                      : t("durable.scheduleReview")}
              </Button>
            ) : !query.data.schedule ? (
              <Button
                type="button"
                size="sm"
                disabled={
                  scheduleBusy ||
                  recoveryBlocked ||
                  storageError ||
                  agentPending !== null ||
                  pendingAgentActions.length > 0 ||
                  runningAgentActions.length > 0 ||
                  uncertainAgentActions.length > 0
                }
                onClick={() => void proposeCheckIn()}
              >
                {scheduleBusy ? t("durable.scheduleWorking") : t("durable.schedulePropose")}
              </Button>
            ) : null}
            {approvalChat && pendingApprovalActions.length > 0 && (
              <ol className="space-y-2">
                {pendingApprovalActions.map((action) => (
                  <ActionCard
                    key={`${approvalChat.id}-${action.id}`}
                    action={action}
                    busy={scheduleBusy}
                    proposalTitle={
                      action.arguments?.action === "pause"
                        ? t("durable.pauseActionTitle")
                        : action.arguments?.action === "resume"
                          ? t("durable.resumeActionTitle")
                          : t("durable.scheduleActionTitle")
                    }
                    onDecide={(confirm) => void decideSchedule(action.id, confirm)}
                    onAuthorizeMcp={
                      action.arguments?.action === "pause" ? undefined : () => void authorizeScheduleForMcp(action.id)
                    }
                  />
                ))}
              </ol>
            )}
            {staleApprovalActions.map((action) => (
              <div key={action.id} className="space-y-2 rounded border border-warn p-3 text-sm" role="alert">
                <p>{t("durable.scheduleReviewStale")}</p>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={scheduleBusy}
                  onClick={() => void decideSchedule(action.id, false)}
                >
                  {t("durable.scheduleDiscardStale")}
                </Button>
              </div>
            ))}
            {runningApprovalActions.map((action) => (
              <div key={action.id} className="space-y-2 rounded border border-warn p-3 text-sm" role="status">
                <p>{t("durable.scheduleApprovalInterrupted")}</p>
                <Button type="button" size="sm" disabled={scheduleBusy} onClick={() => void recoverSchedule(action.id)}>
                  {scheduleBusy ? t("durable.scheduleWorking") : t("durable.scheduleCheckOutcome")}
                </Button>
              </div>
            ))}
            {uncertainApprovalActions.length > 0 && (
              <div className="space-y-2" role="alert">
                {uncertainApprovalActions.map((action) => (
                  <p key={action.id} className="text-sm text-bad">
                    {action.result || t("durable.scheduleOutcomeUnknown")}
                  </p>
                ))}
                <Button type="button" size="sm" variant="outline" onClick={forgetApprovalChat}>
                  {t("durable.scheduleRecoveryAcknowledged")}
                </Button>
              </div>
            )}
            {approvalChatId && approvalQuery.isPending && <p role="status">{t("durable.scheduleLoadingReview")}</p>}
            {approvalChatId && !approvalQuery.isPending && !approvalChat && (
              <p role="alert">{t("durable.scheduleReviewFailed")}</p>
            )}
            {intentNotice && <p role="status">{t("durable.scheduleIntentPrepared")}</p>}
            {scheduleError && <p role="alert">{scheduleErrorMessage || t("durable.scheduleFailed")}</p>}
          </section>
        )}
      </div>
      <form
        className="space-y-2 border-t border-border p-3"
        onSubmit={(event) => {
          event.preventDefault();
          void send();
        }}
      >
        <p className="text-xs text-muted-foreground">{t("durable.queued")}</p>
        {recoveryBlocked ? (
          <div className="space-y-2 text-sm" role="alert">
            <p>{t("durable.draftUnreadable")}</p>
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() => {
                if (resolveRecovery()) setError(false);
              }}
            >
              {t("durable.clearUnreadableDraft")}
            </Button>
          </div>
        ) : storageError ? (
          <p className="text-sm" role="alert">
            {t("durable.preservationFailed")}
          </p>
        ) : null}
        <label className="block space-y-1 text-sm">
          <span>{t("durable.message")}</span>
          <textarea
            ref={composerInput}
            className="w-full rounded border border-border bg-background p-2"
            value={text}
            maxLength={4000}
            disabled={busy || pending !== null || agentPending !== null || !query.data || recoveryBlocked}
            onChange={(event) => updateComposer({ text: event.target.value })}
          />
        </label>
        {error && pending?.outcome !== "subscription_usage_limit" && <p role="alert">{t("durable.failed")}</p>}
        {pending?.outcome === "subscription_usage_limit" ? (
          <p role="status" className="text-sm">
            Claude Code reported its subscription usage limit. Your message is saved; the answer is uncertain and this
            turn will not run again. This conversation remains blocked until the uncertain turn is resolved.
          </p>
        ) : (
          pending?.accepted && (
            <p role="status" className="text-sm">
              Your message was saved. Its answer is not confirmed. Retrying uses the same request; an interrupted turn
              will not be run again.
            </p>
          )
        )}
        {pending?.accepted && !busy && (
          <UncertainTurnRecovery
            key={`${sessionId}:${pending.request_id}`}
            sessionId={sessionId}
            requestId={pending.request_id}
            onResolved={() => {
              updateComposer({ pending: null, text: "" });
              void client.invalidateQueries({ queryKey: ["heinzel", "session", sessionId] });
              void client.invalidateQueries({ queryKey: ["heinzel", "tool-activity", sessionId] });
              void client.invalidateQueries({ queryKey: ["heinzel", "input-snapshot-history", sessionId] });
              void client.invalidateQueries({ queryKey: ["heinzel", "agent-proposal", sessionId] });
              void client.invalidateQueries({ queryKey: ["heinzel", "profile-proposal", sessionId] });
              void client.invalidateQueries({ queryKey: ["heinzel", "latest-schedule-suggestion", sessionId] });
              void client.invalidateQueries({ queryKey: ["heinzel", "latest-source-suggestion", sessionId] });
              void client.invalidateQueries({ queryKey: ["heinzel", "connected-read-set", sessionId] });
            }}
          />
        )}
        <Button
          type="submit"
          disabled={
            busy ||
            storageError ||
            recoveryBlocked ||
            agentChatUnavailable ||
            !query.data ||
            pending?.outcome === "subscription_usage_limit" ||
            (!pending && !agentPending && !text.trim())
          }
        >
          {busy ? t("durable.scheduleWorking") : pending || agentPending ? t("durable.retry") : t("durable.send")}
        </Button>
      </form>
    </div>
  );
}

function DaemonTurnInspection({
  sessionId,
  requestId,
  onResolved,
}: {
  sessionId: string;
  requestId: string;
  onResolved: () => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <Button type="button" size="sm" variant="outline" onClick={() => setOpen(!open)}>
        {open ? "Hide turn state" : "Inspect daemon turn"}
      </Button>
      {open && (
        <UncertainTurnRecovery sessionId={sessionId} requestId={requestId} allowPendingDaemon onResolved={onResolved} />
      )}
    </div>
  );
}
