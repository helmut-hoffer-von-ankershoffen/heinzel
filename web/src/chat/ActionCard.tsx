// A proposed action as a card in a chat: it runs only on Confirm (ADR 0045).
import { Check, X } from "lucide-react";
import { type ReactNode, useId } from "react";
import type { SchemaChatAction } from "@/api/client";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n";

export function ActionCard({
  action,
  busy,
  onDecide,
  onAuthorizeMcp,
  proposalTitle,
  reviewDetails,
}: {
  action: SchemaChatAction;
  busy: boolean;
  onDecide: (confirm: boolean) => void;
  onAuthorizeMcp?: () => void;
  proposalTitle?: string;
  reviewDetails?: ReactNode;
}) {
  const titleId = useId();
  return (
    <li className="rounded-lg border border-warn bg-muted p-3 text-sm">
      <section aria-labelledby={titleId}>
        <h3 id={titleId} className="font-semibold">
          {proposalTitle ?? t("chat.proposal")}
        </h3>
        <p className="mt-1 text-xs text-muted-foreground">{t("chat.proposalHelp")}</p>
        <code className="mt-2 block break-all rounded bg-background p-2 text-xs">{action.command}</code>
        {reviewDetails ??
          (Object.keys(action.arguments ?? {}).length > 0 && (
            <>
              <p className="mt-2 text-xs text-muted-foreground">{t("chat.arguments")}</p>
              <pre className="mt-1 overflow-x-auto whitespace-pre-wrap break-all rounded bg-background p-2 text-xs">
                {JSON.stringify(action.arguments, null, 2)}
              </pre>
            </>
          ))}
        <div className="mt-2 flex gap-2">
          {onAuthorizeMcp && (
            <Button size="sm" variant="outline" disabled={busy} onClick={onAuthorizeMcp}>
              {action.mcp_authorized ? t("chat.refreshMcpAuthorization") : t("chat.authorizeMcp")}
            </Button>
          )}
          {action.mcp_authorized && <span role="status">{t("chat.authorizedMcp")}</span>}
          <Button size="sm" disabled={busy} onClick={() => onDecide(true)}>
            <Check aria-hidden />
            {t("chat.confirm")}
          </Button>
          <Button size="sm" variant="outline" disabled={busy} onClick={() => onDecide(false)}>
            <X aria-hidden />
            {t("chat.decline")}
          </Button>
        </div>
      </section>
    </li>
  );
}
