import { Check, Copy, Trash2 } from "lucide-react";
import { useState } from "react";
import {
  type AgentActivity,
  activityOf,
  claudeAddLine,
  type McpMade,
  useAgentWrites,
  useMakeMcpToken,
  useMcp,
  useRevokeMcpToken,
  useSwitchMcp,
} from "@/api/mcp";
import { Loaded } from "@/components/common";
import { Button } from "@/components/ui/button";
import { format, t } from "@/i18n";
import { Switch } from "./SettingControls";

/** A copy button that says so for a moment. */
function CopyButton({ text, label }: { text: string; label: string }) {
  const [done, setDone] = useState(false);
  const copy = async () => {
    await navigator.clipboard.writeText(text);
    setDone(true);
    window.setTimeout(() => setDone(false), 1500);
  };
  return (
    <Button type="button" variant="ghost" size="sm" aria-label={label} title={label} onClick={() => void copy()}>
      {done ? <Check aria-hidden className="size-4" /> : <Copy aria-hidden className="size-4" />}
      <span className="sr-only" role="status">
        {done ? t("mcp.copied") : ""}
      </span>
    </Button>
  );
}

/** What a token's agent changed, as hz recorded it: a write token always has the line, a read token only if it tried. */
function TokenWrites({ activity, write }: Readonly<{ activity: AgentActivity | undefined; write: boolean }>) {
  if (!activity && !write) return null;
  const refused = activity?.refused ?? 0;
  return (
    <p className="text-muted-foreground" data-token-writes>
      {activity?.ran ? t("mcp.writes", { today: activity.ran_today, total: activity.ran }) : t("mcp.noWrites")}
      {refused > 0 && ` ${t("mcp.writesRefused", { count: refused })}`}
    </p>
  );
}

/** Settings, MCP (ADR 0162): the one switch for MCP at /mcp, and the tokens of the agents that connect. */
export function McpSettings() {
  const state = useMcp();
  const toggle = useSwitchMcp();
  const make = useMakeMcpToken();
  const revoke = useRevokeMcpToken();
  const writes = useAgentWrites();
  const [actor, setActor] = useState("");
  const [scope, setScope] = useState<"read" | "write">("read");
  const [made, setMade] = useState<McpMade | null>(null);
  const origin = window.location.origin;
  const submit = () => {
    if (!actor.trim() || make.isPending) return;
    make.mutate(
      { actor: actor.trim(), scope },
      {
        onSuccess: (token) => {
          setMade(token);
          setActor("");
        },
      },
    );
  };
  return (
    <section aria-label={t("mcp.title")} className="space-y-4 text-sm">
      <h3 className="text-ui font-medium">{t("mcp.title")}</h3>
      <p className="text-muted-foreground">{t("mcp.description")}</p>
      <Loaded query={state}>
        {(mcp) => (
          <>
            <div className="flex items-center justify-between gap-4">
              <span id="mcp-switch">{t("mcp.switch")}</span>
              <Switch
                labelledBy="mcp-switch"
                on={mcp.enabled}
                disabled={toggle.isPending}
                onToggle={() => toggle.mutate(!mcp.enabled)}
              />
            </div>
            <p role="status" className="text-muted-foreground">
              {mcp.enabled ? t("mcp.on", { url: `${origin}/mcp` }) : t("mcp.off")}
            </p>
            {toggle.error && <p className="text-warn">{toggle.error.message}</p>}
            {mcp.enabled && (
              <>
                <h4 className="font-medium">{t("mcp.tokens")}</h4>
                {mcp.tokens.length === 0 ? (
                  <p className="text-muted-foreground">{t("mcp.noTokens")}</p>
                ) : (
                  <ul className="divide-y divide-border">
                    {mcp.tokens.map((item) => (
                      <li key={item.id} className="flex items-center justify-between gap-3 py-2">
                        <div>
                          <p>
                            {item.actor} <span className="text-muted-foreground">({t(`mcp.${item.scope}`)})</span>
                          </p>
                          <p className="text-muted-foreground">
                            {t("mcp.expires", { when: format.date(item.expires) })},{" "}
                            {item.last_used
                              ? t("mcp.lastUsed", { when: format.dateTime(item.last_used) })
                              : t("mcp.neverUsed")}
                          </p>
                          <TokenWrites activity={activityOf(writes.data, item.id)} write={item.scope === "write"} />
                        </div>
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          aria-label={t("mcp.revoke", { actor: item.actor })}
                          title={t("mcp.revoke", { actor: item.actor })}
                          disabled={revoke.isPending}
                          onClick={() => revoke.mutate(item.id)}
                        >
                          <Trash2 aria-hidden className="size-4" />
                        </Button>
                      </li>
                    ))}
                  </ul>
                )}
                <form
                  className="flex flex-wrap items-end gap-3"
                  onSubmit={(event) => {
                    event.preventDefault();
                    submit();
                  }}
                >
                  <label className="space-y-1">
                    <span className="block text-muted-foreground">{t("mcp.actor")}</span>
                    <input
                      value={actor}
                      onChange={(event) => setActor(event.target.value)}
                      placeholder={t("mcp.actorHint")}
                      maxLength={40}
                      className="h-9 rounded-md border border-border bg-transparent px-2"
                    />
                  </label>
                  <label className="space-y-1">
                    <span className="block text-muted-foreground">{t("mcp.scope")}</span>
                    <select
                      value={scope}
                      onChange={(event) => setScope(event.target.value === "write" ? "write" : "read")}
                      className="h-9 rounded-md border border-border bg-transparent px-2"
                    >
                      <option value="read">{t("mcp.read")}</option>
                      <option value="write">{t("mcp.write")}</option>
                    </select>
                  </label>
                  <Button type="submit" disabled={!actor.trim() || make.isPending}>
                    {t("mcp.make")}
                  </Button>
                </form>
                {make.error && <p className="text-warn">{make.error.message}</p>}
                {made && (
                  <fieldset className="space-y-2 rounded-md border border-border p-3">
                    <legend className="sr-only">{t("mcp.connect")}</legend>
                    <p>{t("mcp.once")}</p>
                    <div className="flex items-center gap-2">
                      <code className="min-w-0 flex-1 break-all">{made.token}</code>
                      <CopyButton text={made.token} label={t("mcp.copyToken")} />
                    </div>
                    <p className="font-medium">{t("mcp.connect")}</p>
                    <div className="flex items-center gap-2">
                      <code className="min-w-0 flex-1 break-all">{claudeAddLine(origin, made.token)}</code>
                      <CopyButton text={claudeAddLine(origin, made.token)} label={t("mcp.copyCommand")} />
                    </div>
                    <Button type="button" variant="ghost" onClick={() => setMade(null)}>
                      {t("mcp.done")}
                    </Button>
                  </fieldset>
                )}
                {!made && (
                  <div className="flex items-center gap-2">
                    <code className="min-w-0 flex-1 break-all">{claudeAddLine(origin)}</code>
                    <CopyButton text={claudeAddLine(origin)} label={t("mcp.copyCommand")} />
                  </div>
                )}
                <p className="text-muted-foreground">{t("mcp.sharedNote")}</p>
              </>
            )}
          </>
        )}
      </Loaded>
    </section>
  );
}
