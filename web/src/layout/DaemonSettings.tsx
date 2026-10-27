import { useState } from "react";
import { type DaemonAction, useChangeDaemon, useDaemonStatus } from "@/api/daemon";
import { Loaded } from "@/components/common";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n";

type Action = DaemonAction;

/** Controls the daemon after explicit confirmation; install and uninstall change persistent login behavior. */
export function DaemonSettings() {
  const status = useDaemonStatus();
  const change = useChangeDaemon();
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
                <dt className="text-muted-foreground">{t("daemon.jobs")}</dt>
                <dd>{daemon.jobs}</dd>
                <dt className="text-muted-foreground">{t("daemon.lastTick")}</dt>
                <dd>{daemon.last_tick ? new Date(daemon.last_tick).toLocaleString() : t("daemon.never")}</dd>
              </dl>
              {daemon.version_mismatch && <p className="text-warn">{t("daemon.mismatch")}</p>}
              {!supported && <p>{t("daemon.unsupported")}</p>}
              <div className="flex flex-wrap gap-2">
                {supported &&
                  (daemon.installed ? (
                    <>
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={change.isPending}
                        onClick={() => ask(daemon.paused ? "resume" : "pause")}
                      >
                        {label(daemon.paused ? "resume" : "pause")}
                      </Button>
                      {(daemon.version_mismatch || !daemon.version || !daemon.loaded) && (
                        <Button size="sm" variant="outline" disabled={change.isPending} onClick={() => ask("restart")}>
                          {label("restart")}
                        </Button>
                      )}
                      <Button size="sm" variant="outline" disabled={change.isPending} onClick={() => ask("uninstall")}>
                        {label("uninstall")}
                      </Button>
                    </>
                  ) : (
                    <Button size="sm" variant="outline" disabled={change.isPending} onClick={() => ask("install")}>
                      {label("install")}
                    </Button>
                  ))}
                <Button size="sm" variant="ghost" disabled={status.isFetching} onClick={() => void status.refetch()}>
                  {t("daemon.refresh")}
                </Button>
              </div>
            </>
          );
        }}
      </Loaded>
      {change.error && (
        <p role="alert" className="text-bad">
          {change.error.message}
        </p>
      )}
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
          <div className="flex gap-2">
            <Button size="sm" variant="outline" autoFocus onClick={() => setPending(null)}>
              {t("contrib.cancel")}
            </Button>
            <Button size="sm" disabled={change.isPending} onClick={confirm}>
              {label(pending)}
            </Button>
          </div>
        </div>
      )}
    </section>
  );
}
