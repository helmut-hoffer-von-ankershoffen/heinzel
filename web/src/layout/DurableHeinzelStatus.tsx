import { useQuery } from "@tanstack/react-query";
import { Bot, RefreshCw } from "lucide-react";
import { useRef, useState } from "react";
import { Link, useNavigate } from "react-router";
import { api, unwrap } from "@/api/client";
import { FooterItem, type State } from "@/components/FooterItem";
import { t } from "@/i18n";

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
  const navigate = useNavigate();
  const createRequest = useRef<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [createFailed, setCreateFailed] = useState(false);
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

  const create = async () => {
    if (creating) return;
    createRequest.current ??= crypto.randomUUID();
    setCreating(true);
    setCreateFailed(false);
    try {
      const created = await unwrap(
        api.POST("/heinzel/new-chat", { body: { request_id: createRequest.current, name: "Personal helper" } }),
      );
      createRequest.current = null;
      navigate(`/heinzel?chat=${created.session_id}`);
    } catch {
      setCreateFailed(true);
    } finally {
      setCreating(false);
    }
  };

  return (
    <FooterItem
      label={label}
      triggerLabel={`${count ?? (query.isError ? "?" : "…")} ${label}`}
      state={state}
      content={
        <span aria-hidden className="inline-flex items-center gap-0.5 font-mono text-xs tabular-nums">
          <Bot className="size-4" />
          {count ?? (query.isError ? "?" : "…")}
        </span>
      }
    >
      <div className="space-y-2 px-1.5 pb-1.5">
        {query.isPending ? (
          <p role="status">{t("state.loading")}</p>
        ) : query.isError ? (
          <>
            <p role="status">{t("health.failed")}</p>
            <button
              type="button"
              disabled={query.isFetching}
              onClick={() => void query.refetch()}
              className="inline-flex min-h-6 items-center gap-1 rounded px-1 hover:bg-accent disabled:opacity-50"
            >
              <RefreshCw aria-hidden className="size-3" />
              {t("error.retry")}
            </button>
          </>
        ) : sessions?.length ? (
          <>
            <p>{count} persisted personal Heinzels</p>
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
          <>
            <p>No persisted Heinzels yet.</p>
            <button
              type="button"
              disabled={creating}
              onClick={() => void create()}
              className="inline-flex min-h-7 items-center rounded px-2 text-sm underline hover:bg-accent disabled:opacity-50"
            >
              {creating ? t("durable.scheduleWorking") : t("durable.create")}
            </button>
            {createFailed && <p role="alert">{t("durable.createFailed")}</p>}
          </>
        )}
        <Link to="/" className="block border-t border-border pt-2 underline">
          {t("status.open")} chat
        </Link>
      </div>
    </FooterItem>
  );
}
