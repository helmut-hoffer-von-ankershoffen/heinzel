// Settings > Notifications > Also in chat (ADR 0160): notices and a Heinzel's questions also in your own chat messages.
// Each switch is offered only when the bound chat connector can carry it, and names that connector; the app keeps
// showing everything. The switches are `[system.notify] chat_notifications` and `chat_questions`, read afresh.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useId } from "react";
import { api, unwrap } from "@/api/client";
import { useSetSetting } from "@/api/queries";
import { t } from "@/i18n";
import { Switch } from "@/layout/SettingControls";
import { inWords } from "@/layout/settingsSections";

export const SELF_CHAT_KEY = ["system", "notify", "chat"];

/** How notices and questions reach you in chat, and through which connector. */
export const useSelfChat = () =>
  useQuery({ queryKey: SELF_CHAT_KEY, queryFn: () => unwrap(api.GET("/system/notify/chat")) });

/** The connector's name as people say it: `slack` → `Slack`. */
export const connectorName = (adapter: string) => inWords(adapter).replace(/^./, (first) => first.toUpperCase());

type Use = "notifications" | "questions";

export function ChatChannelSettings() {
  const status = useSelfChat();
  const save = useSetSetting();
  const client = useQueryClient();
  const headingId = useId();
  const data = status.data;
  if (!data) return null;
  const toggle = (use: Use, on: boolean) =>
    save.mutate(
      { key: `system.notify.chat_${use}`, value: on ? "true" : "false" },
      { onSettled: () => client.invalidateQueries({ queryKey: SELF_CHAT_KEY }) },
    );
  const offered = (["notifications", "questions"] as const).filter((use) => data[use].supported);
  return (
    <section aria-labelledby={headingId} className="mt-6">
      <h4 id={headingId} className="mb-1 text-ui-sm text-muted-foreground">
        {t("selfChat.title")}
      </h4>
      {data.adapter === null || offered.length === 0 ? (
        <p className="text-sm text-muted-foreground">{data.detail}</p>
      ) : (
        <>
          <p className="text-sm text-muted-foreground">
            {t("selfChat.uses", { connector: connectorName(data.adapter) })}
          </p>
          <ul>
            {offered.map((use) => (
              <ChatRow
                key={use}
                use={use}
                on={data[use].on}
                connector={connectorName(data.adapter ?? "")}
                minutes={data.wait_minutes}
                disabled={save.isPending}
                onToggle={(on) => toggle(use, on)}
              />
            ))}
          </ul>
          {save.error && (
            <p role="alert" className="mt-1 text-xs text-bad">
              {save.error.message}
            </p>
          )}
        </>
      )}
    </section>
  );
}

function ChatRow({
  use,
  on,
  connector,
  minutes,
  disabled,
  onToggle,
}: {
  use: Use;
  on: boolean;
  connector: string;
  minutes: number;
  disabled: boolean;
  onToggle: (on: boolean) => void;
}) {
  const labelId = useId();
  return (
    <li className="flex items-center gap-6 border-b border-border/70 py-3">
      <div className="min-w-0 flex-1">
        <p id={labelId} className="text-ui">
          {t(`selfChat.${use}`)}
        </p>
        <p className="mt-0.5 text-sm text-muted-foreground">
          {t(`selfChat.${use}Help`, { connector, minutes: String(minutes) })}
        </p>
      </div>
      <Switch labelledBy={labelId} on={on} disabled={disabled} onToggle={() => onToggle(!on)} />
    </li>
  );
}

/** Under an owner's message that came from chat: where it was answered, so the chat reads as it happened. */
export function AnsweredInChat() {
  const status = useSelfChat();
  const adapter = status.data?.adapter;
  return (
    <p className="text-xs text-muted-foreground">
      {adapter ? t("selfChat.answeredIn", { connector: connectorName(adapter) }) : t("selfChat.answeredInChat")}
    </p>
  );
}
