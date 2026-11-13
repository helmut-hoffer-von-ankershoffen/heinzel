import { useQuery } from "@tanstack/react-query";
import { Bot, MessageSquare, Plus, RefreshCw } from "lucide-react";
import { useSearchParams } from "react-router";
import { api, unwrap } from "@/api/client";
import { FooterItem, type State } from "@/components/FooterItem";
import { HeinzelAvatar } from "@/components/HeinzelAvatar";
import { PaneAction } from "@/components/PaneActions";
import { appearanceLook, stateLabel } from "@/heinzel/look";
import { t } from "@/i18n";
import { createDraftPath } from "@/lib/chatIntent";

const useDurableHeinzels = () =>
  useQuery({
    queryKey: ["heinzel", "sessions"],
    queryFn: () => unwrap(api.GET("/heinzel/sessions")),
    refetchInterval: 30_000,
    refetchOnWindowFocus: true,
    retry: false,
  });

export function DurableHeinzelStatus() {
  const query = useDurableHeinzels();
  const [params] = useSearchParams();
  const sessions = query.isError ? undefined : query.data?.sessions;
  const count = sessions?.length;
  const active = sessions?.filter((session) => session.state === "awake").length;
  const paused = sessions?.filter((session) => session.state === "paused").length;
  const attention = sessions?.filter((session) => session.state === "waiting").length;
  const proposed = sessions?.filter((session) => session.state === "proposed").length;
  const asleep = sessions?.filter((session) => session.state === "asleep").length;
  const retired = sessions?.filter((session) => session.state === "retired").length;
  const state: State = query.isError ? "bad" : "off";
  const label = t("nav.heinzelmaennchen");

  return (
    <FooterItem
      label={label}
      triggerLabel={`${count ?? (query.isError ? "?" : "…")} ${label}`}
      state={state}
      content={
        <span aria-hidden className="inline-flex items-center gap-0.5 font-mono text-xs tabular-nums">
          {sessions?.length ? (
            // Up to three faces say whose they are; the number says how many.
            <span className="inline-flex -space-x-1.5">
              {sessions.slice(0, 3).map((session) => (
                <HeinzelAvatar
                  key={session.session_id}
                  view="bust"
                  {...appearanceLook(session.appearance)}
                  className="size-4 overflow-hidden"
                />
              ))}
            </span>
          ) : (
            <Bot className="size-4" />
          )}
          {count ?? (query.isError ? "?" : "…")}
        </span>
      }
      actions={
        <>
          {query.isError ? (
            <PaneAction icon={RefreshCw} disabled={query.isFetching} onClick={() => void query.refetch()}>
              {t("error.retry")}
            </PaneAction>
          ) : !query.isPending && !sessions?.length ? (
            // The page's draft asks for a name and a look first; nothing exists until it is saved.
            <PaneAction icon={Plus} primary to={createDraftPath(params)}>
              {t("durable.create")}
            </PaneAction>
          ) : null}
          <PaneAction icon={MessageSquare} to="/">
            {t("durable.openChat")}
          </PaneAction>
        </>
      }
    >
      <div className="space-y-2 px-1.5 pb-1.5">
        {query.isPending ? (
          <p role="status">{t("state.loading")}</p>
        ) : query.isError ? (
          <p role="status">{t("health.failed")}</p>
        ) : sessions?.length ? (
          <>
            <p>{count} persisted personal Heinzels</p>
            <ul aria-label={t("chat.personalSessions")} className="max-h-48 space-y-0.5 overflow-y-auto">
              {sessions.map((session) => (
                <li key={session.session_id}>
                  <div className="flex min-h-8 items-center gap-2 px-1.5 py-1">
                    <HeinzelAvatar
                      view="bust"
                      {...appearanceLook(session.appearance)}
                      className="size-6 shrink-0 overflow-hidden"
                    />
                    <span className="min-w-0 flex-1 truncate">{session.name}</span>
                    <span className="shrink-0 text-xs text-muted-foreground">{stateLabel(session.state)}</span>
                  </div>
                </li>
              ))}
            </ul>
            <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
              <dt>Active</dt>
              <dd>{active}</dd>
              <dt>Paused</dt>
              <dd>{paused}</dd>
              <dt>Needs attention</dt>
              <dd>{attention}</dd>
              <dt>Proposed</dt>
              <dd>{proposed}</dd>
              <dt>Asleep</dt>
              <dd>{asleep}</dd>
              <dt>Retired</dt>
              <dd>{retired}</dd>
            </dl>
          </>
        ) : (
          <p>No persisted Heinzels yet.</p>
        )}
      </div>
    </FooterItem>
  );
}
