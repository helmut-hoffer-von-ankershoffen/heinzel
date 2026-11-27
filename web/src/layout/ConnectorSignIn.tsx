import { useIsMutating, useMutation, useMutationState, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useSyncExternalStore } from "react";
import { api, authorityMiddleware, unwrap } from "@/api/client";
import { signInFlowMissing, useSignInFlow } from "@/api/queries";
import { authorityEpoch, subscribeAuthority } from "@/api/token";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n";
import { isLocalServer } from "@/lib/browser";

const OUTCOME = {
  succeeded: "signIn.ended.succeeded",
  failed: "signIn.ended.failed",
  cancelled: "signIn.ended.cancelled",
  "timed-out": "signIn.ended.timedOut",
} as const;

export function ConnectorSignIn({ name, title }: Readonly<{ name: string; title: string }>) {
  const local = isLocalServer();
  const statusRef = useRef<HTMLParagraphElement>(null);
  const focusProgress = useRef(false);
  const client = useQueryClient();
  const epoch = useSyncExternalStore(subscribeAuthority, authorityEpoch);
  const key = ["system", "connector", "connect", name, epoch];
  const pending = useIsMutating({ mutationKey: key, exact: true }) > 0;
  // Both calendar surfaces can reopen a sign-in this browser started under the same authority.
  const flowIds = useMutationState({
    filters: { mutationKey: key, exact: true, status: "success" },
    select: ({ state }) => (state.data as { id: string } | undefined)?.id,
  });
  const id = local ? flowIds.at(-1) : undefined;
  const flow = useSignInFlow(id ?? null, epoch);
  const connect = useMutation({
    mutationKey: key,
    // Keep the browser's flow id across pane changes for the server's 15-minute consent window.
    gcTime: 16 * 60 * 1000,
    mutationFn: () =>
      unwrap(
        api.POST("/system/connector/connect", {
          body: { name, wait: false },
          middleware: authorityMiddleware(epoch),
        }),
      ),
  });
  const step = useMutation({
    mutationFn: ({ action, id, epoch }: { action: "open" | "cancel"; id: string; epoch: number }) =>
      unwrap(api.POST(`/system/connector/${action}`, { body: { flow: id }, middleware: authorityMiddleware(epoch) })),
    onSuccess: () => void flow.refetch(),
  });
  const missing = signInFlowMissing(flow.error);
  const state = flow.data?.state;
  const running =
    id && !missing && (state === undefined || state === "waiting" || state === "starting") ? { id, epoch } : null;
  const active = running !== null;
  useEffect(() => {
    if (active && focusProgress.current) {
      focusProgress.current = false;
      if (document.activeElement === document.body) statusRef.current?.focus({ preventScroll: true });
    }
  }, [active]);
  const outcome = state && state !== "starting" && state !== "waiting" ? OUTCOME[state] : null;
  useEffect(() => {
    if (state === "succeeded") {
      void client.invalidateQueries({ queryKey: ["system", "connector", "list"] });
      void client.invalidateQueries({ queryKey: ["org", "calendar"] });
      void client.invalidateQueries({ queryKey: ["org", "agenda"] });
    }
  }, [state, client]);

  if (!local) return <p>{t("signIn.otherDevice", { title })}</p>;
  return (
    <div className="space-y-2">
      {outcome && !missing && !connect.isError && <p role="status">{flow.data?.message || t(outcome)}</p>}
      {connect.isError && <p role="alert">{t("signIn.couldNotStart", { title })}</p>}
      {flow.isError && (
        <p role="alert">{t(missing ? "calendar.signInFlowMissing" : "calendar.signInStatusUnavailable")}</p>
      )}
      {step.isError && <p role="alert">{t("calendar.signInActionFailed")}</p>}
      {running ? (
        <>
          <p ref={statusRef} role="status" tabIndex={-1}>
            {t(state === "waiting" ? "signIn.waiting" : "signIn.starting", { title })}
          </p>
          {state === "waiting" && flow.data?.progress?.code?.trim() && (
            <p className="break-words">
              {t("signIn.enterCode", { title })} <code>{flow.data.progress.code}</code>
            </p>
          )}
          <div className="flex flex-wrap gap-2">
            {state === "waiting" && flow.data?.progress && (
              <Button
                type="button"
                className="min-h-11 min-w-11"
                disabled={step.isPending}
                onClick={() => step.mutate({ action: "open", ...running })}
              >
                {t("signIn.openPage", { title })}
              </Button>
            )}
            <Button
              type="button"
              className="min-h-11 min-w-11"
              variant="outline"
              disabled={step.isPending}
              onClick={() => step.mutate({ action: "cancel", ...running })}
            >
              {t("signIn.cancel")}
            </Button>
          </div>
        </>
      ) : (
        <Button
          type="button"
          className="min-h-11 min-w-11"
          disabled={pending}
          onClick={(event) => {
            focusProgress.current = document.activeElement === event.currentTarget;
            step.reset();
            connect.mutate();
          }}
        >
          {t("signIn.button", { title })}
        </Button>
      )}
    </div>
  );
}
