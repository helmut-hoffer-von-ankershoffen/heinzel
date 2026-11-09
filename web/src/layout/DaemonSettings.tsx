import { Check, Download, Pause, Play, RefreshCw, RotateCw, Trash2, X } from "lucide-react";
import { useState } from "react";
import { type DaemonAction, useChangeDaemon, useDaemonStatus } from "@/api/daemon";
import { Loaded } from "@/components/common";
import { OperationDialog } from "@/components/OperationDialog";
import { PaneAction, PaneActions } from "@/components/PaneActions";
import { t } from "@/i18n";
import { DAEMON_RESTART } from "@/lib/operationCalls";
import { requestOperation, useOperationRun } from "@/lib/operationRun";

type Action = DaemonAction;

/** Controls the daemon after explicit confirmation; install and uninstall change persistent login behavior. */
export function DaemonSettings() {
  const status = useDaemonStatus();
  const change = useChangeDaemon();
  const operation = useOperationRun();
  const operationBusy = operation.stage === "running" || operation.stage === "confirm";
  const [pending, setPending] = useState<Action | null>(null);
  const label = (action: Action) => t(`daemon.${action}`);
  const ask = (action: Action) => {
    change.reset();
    setPending(action);
  };
  const confirm = () => {
    if (!pending || change.isPending) return;
    const selected = pending;
    setPending(null);
    change.mutate(selected);
  };
  return (
    <section aria-label={t("daemon.controls")} className="space-y-3 text-sm">
      <Loaded query={status}>
        {(daemon) => {
          const supported = daemon.platform === "darwin" || daemon.platform === "linux";
          return (
            <>
              <p role="status">
                {t(
                  !daemon.installed
                    ? "daemon.absent"
                    : daemon.paused
                      ? "daemon.paused"
                      : !daemon.loaded
                        ? "daemon.unloaded"
                        : daemon.failing.length
                          ? "daemon.attention"
                          : "daemon.running",
                )}
              </p>
              <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1">
                <dt className="text-muted-foreground">{t("daemon.version")}</dt>
                <dd>{daemon.version ?? t("daemon.unknown")}</dd>
                <dt className="text-muted-foreground">{t("daemon.installedVersion")}</dt>
                <dd>{daemon.installed_version ?? t("daemon.unknown")}</dd>
                <dt className="text-muted-foreground">{t("daemon.jobs")}</dt>
                <dd>{daemon.jobs}</dd>
                <dt className="text-muted-foreground">{t("daemon.lastTick")}</dt>
                <dd>{daemon.last_tick ? new Date(daemon.last_tick).toLocaleString() : t("daemon.never")}</dd>
              </dl>
              {daemon.version_mismatch && <p className="text-warn">{t("daemon.mismatch")}</p>}
              {!supported && <p>{t("daemon.unsupported")}</p>}
              <PaneActions>
                {supported &&
                  (daemon.installed ? (
                    <PaneAction
                      key="pause"
                      icon={daemon.paused ? Play : Pause}
                      disabled={change.isPending || operationBusy}
                      onClick={() => ask(daemon.paused ? "resume" : "pause")}
                    >
                      {label(daemon.paused ? "resume" : "pause")}
                    </PaneAction>
                  ) : (
                    <PaneAction
                      key="install"
                      icon={Download}
                      disabled={change.isPending || operationBusy}
                      onClick={() => ask("install")}
                    >
                      {label("install")}
                    </PaneAction>
                  ))}
                {supported && daemon.installed && (daemon.version_mismatch || !daemon.version || !daemon.loaded) && (
                  <PaneAction
                    key="restart"
                    icon={RotateCw}
                    disabled={change.isPending || operationBusy}
                    onClick={() => {
                      change.reset();
                      requestOperation(DAEMON_RESTART);
                    }}
                  >
                    {label("restart")}
                  </PaneAction>
                )}
                <PaneAction
                  key="refresh"
                  icon={RefreshCw}
                  disabled={status.isFetching}
                  onClick={() => void status.refetch()}
                >
                  {t("daemon.refresh")}
                </PaneAction>
                {supported && daemon.installed && (
                  <PaneAction
                    key="uninstall"
                    icon={Trash2}
                    destructive
                    disabled={change.isPending || operationBusy}
                    onClick={() => ask("uninstall")}
                  >
                    {label("uninstall")}
                  </PaneAction>
                )}
              </PaneActions>
            </>
          );
        }}
      </Loaded>
      {change.error && (
        <p role="alert" className="text-bad">
          {change.error.message}
        </p>
      )}
      <OperationDialog />
      {pending && (
        <div
          role="alertdialog"
          aria-label={t("daemon.confirm", { action: label(pending) })}
          className="space-y-3 rounded-xl border border-border p-3"
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              event.stopPropagation();
              setPending(null);
            }
          }}
        >
          <p>{t("daemon.confirm", { action: label(pending) })}</p>
          <code className="block break-all">hz system daemon {pending}</code>
          {(pending === "install" || pending === "uninstall") && <p>{t("daemon.persistenceHint")}</p>}
          <PaneActions align="end" words="all">
            <PaneAction key="cancel" icon={X} autoFocus onClick={() => setPending(null)}>
              {t("contrib.cancel")}
            </PaneAction>
            <PaneAction
              key="confirm"
              icon={Check}
              primary
              destructive={pending === "uninstall"}
              disabled={change.isPending || operationBusy}
              onClick={confirm}
            >
              {label(pending)}
            </PaneAction>
          </PaneActions>
        </div>
      )}
    </section>
  );
}
