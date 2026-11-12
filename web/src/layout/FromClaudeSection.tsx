// Settings → This computer → From Claude: the MCP servers and skills already set up for Claude Code and Claude Desktop
// on this computer (ADR 0062). Secret values are never read, so a server shows the names of the variables and headers
// it is given, not what they hold. A found server is switched on for Heinzels by default, also one from a project or
// a plugin; its switch turns it off for good, and a notice says so until it is closed (ADR 0146). Nothing is started.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { type ReactNode, useId } from "react";
import { api, type SchemaClaudeScan, unwrap } from "@/api/client";
import { Loaded } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/card";
import { type MessageKey, t } from "@/i18n";
import { Switch } from "./SettingControls";

export const FROM_CLAUDE = "from-claude";

type Server = SchemaClaudeScan["servers"][number];
type Skill = SchemaClaudeScan["skills"][number];

const KEY = ["system", "claude", "list"];

const useClaude = () => useQuery({ queryKey: KEY, queryFn: () => unwrap(api.GET("/system/claude/list")) });

/** Switch one server on or off; the list is read again so the switch shows what the server says. The id goes in
 * the body: it can name a project folder, and an address ends up in logs. */
function useSwitch() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ server, on }: { server: string; on: boolean }) =>
      unwrap(api.POST(on ? "/system/claude/enable" : "/system/claude/disable", { body: { server } })),
    onSettled: () => client.invalidateQueries({ queryKey: KEY }),
  });
}

function useAcknowledge() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (servers: string[]) => unwrap(api.POST("/system/claude/acknowledge", { body: { servers } })),
    onSettled: () => client.invalidateQueries({ queryKey: KEY }),
  });
}

/** `code-plugin` + `tools` → "Claude Code, plugin tools": where a group of rows was found. */
const where = (source: string, origin: string | null) =>
  `${t(`fromClaude.source.${source}` as MessageKey)}${origin ? ` · ${origin}` : ""}`;

/** Rows in the order they come, under a heading for each place they were found. */
function Grouped<T extends { source: string; origin: string | null; id: string }>({
  items,
  row,
}: {
  items: T[];
  row: (item: T) => ReactNode;
}) {
  const groups = new Map<string, T[]>();
  for (const item of items) {
    const key = where(item.source, item.origin);
    groups.set(key, [...(groups.get(key) ?? []), item]);
  }
  return (
    <>
      {[...groups].map(([heading, rows]) => (
        <section key={heading} aria-label={heading} className="mb-4">
          <h5 className="mb-1 text-ui-sm text-muted-foreground">
            {heading} ({rows.length})
          </h5>
          <ul className="divide-y divide-border rounded-lg border border-border">
            {rows.map((item) => (
              <li key={item.id} className="px-3 py-2">
                {row(item)}
              </li>
            ))}
          </ul>
        </section>
      ))}
    </>
  );
}

function ServerRow({ server, isNew }: { server: Server; isNew: boolean }) {
  const via = server.host ?? `${server.command ?? "?"} (${server.arguments})`;
  const given = [...server.env_keys, ...server.header_keys];
  const flip = useSwitch();
  const nameId = useId();
  const self = server.status === "self";
  return (
    <div className="flex items-start gap-3">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span id={nameId} className="font-medium text-ui">
            {server.name}
          </span>
          <Badge>{t(`fromClaude.scope.${server.scope}` as MessageKey)}</Badge>
          <Badge>{server.transport}</Badge>
          <Badge>{server.auth}</Badge>
          {self && <Badge tone="ok">{t("fromClaude.self")}</Badge>}
          {isNew && <Badge tone="warn">{t("fromClaude.new")}</Badge>}
        </div>
        <p className="break-all font-mono text-ui-sm text-muted-foreground">{via}</p>
        {given.length > 0 && (
          <p className="text-ui-sm text-muted-foreground">{t("fromClaude.gives", { names: given.join(", ") })}</p>
        )}
        {flip.error && (
          <p role="alert" className="text-ui-sm text-bad">
            {flip.error.message}
          </p>
        )}
      </div>
      {!self && (
        <Switch
          labelledBy={nameId}
          on={server.enabled}
          disabled={flip.isPending}
          onToggle={() => flip.mutate({ server: server.id, on: !server.enabled })}
        />
      )}
    </div>
  );
}

/** Shown until closed: found servers are on without having been asked for, and how many come from elsewhere. */
function Notice({ notice }: { notice: NonNullable<SchemaClaudeScan["notice"]> }) {
  const close = useAcknowledge();
  const count = notice.servers.length;
  return (
    <section
      aria-label={t("fromClaude.notice.title")}
      className="mb-4 flex flex-wrap items-start gap-3 rounded-lg border border-border bg-accent p-3"
    >
      <div className="min-w-0 flex-1 text-sm">
        <p className="font-medium">{t(count === 1 ? "fromClaude.notice.one" : "fromClaude.notice.many", { count })}</p>
        {notice.project + notice.plugin > 0 && (
          <p>{t("fromClaude.notice.scoped", { project: notice.project, plugin: notice.plugin })}</p>
        )}
        <p className="text-muted-foreground">{t("fromClaude.notice.how")}</p>
        {close.error && (
          <p role="alert" className="text-bad">
            {close.error.message}
          </p>
        )}
      </div>
      <Button type="button" size="sm" onClick={() => close.mutate(notice.servers)} disabled={close.isPending}>
        {t("fromClaude.notice.close")}
      </Button>
    </section>
  );
}

function SkillRow({ skill }: { skill: Skill }) {
  const bundled = Object.entries(skill.files).map(([kind, count]) => `${kind} ${count}`);
  return (
    <>
      <span className="font-medium text-ui">{skill.name}</span>
      {skill.description && <p className="text-ui-sm text-muted-foreground">{skill.description}</p>}
      {bundled.length > 0 && (
        <p className="text-ui-sm text-muted-foreground">{t("fromClaude.bundled", { files: bundled.join(", ") })}</p>
      )}
    </>
  );
}

export function FromClaudeSection() {
  const claude = useClaude();
  return (
    <>
      <div className="mb-1 flex items-center justify-between">
        <h3 className="text-ui font-medium">{t("fromClaude.title")}</h3>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => void claude.refetch()}
          disabled={claude.isFetching}
        >
          <RefreshCw aria-hidden className="size-4" />
          {t("fromClaude.rescan")}
        </Button>
      </div>
      <p className="mb-4 text-sm text-muted-foreground">{t("fromClaude.intro")}</p>
      <Loaded query={claude}>
        {(scan) => (
          <>
            {scan.state_problem && (
              <p role="alert" className="mb-4 text-sm text-bad">
                {scan.state_problem}
              </p>
            )}
            {scan.notice && <Notice notice={scan.notice} />}
            <h4 className="mb-2 text-ui font-medium">{t("fromClaude.servers")}</h4>
            {scan.servers.length === 0 ? (
              <p className="mb-4 text-sm text-muted-foreground">{t("fromClaude.noServers")}</p>
            ) : (
              <Grouped
                items={scan.servers}
                row={(server) => (
                  <ServerRow server={server} isNew={scan.notice?.servers.includes(server.id) ?? false} />
                )}
              />
            )}
            <h4 className="mt-6 mb-2 text-ui font-medium">{t("fromClaude.skills")}</h4>
            {scan.skills.length === 0 ? (
              <p className="mb-4 text-sm text-muted-foreground">{t("fromClaude.noSkills")}</p>
            ) : (
              <Grouped items={scan.skills} row={(skill) => <SkillRow skill={skill} />} />
            )}
            {scan.connectors > 0 && (
              <p className="mt-4 text-sm text-muted-foreground">
                {t("fromClaude.connectors", { count: scan.connectors })}
              </p>
            )}
            {scan.skipped.length > 0 && (
              <section aria-label={t("fromClaude.skipped")} className="mt-4">
                <h5 className="mb-1 text-ui-sm text-muted-foreground">{t("fromClaude.skipped")}</h5>
                <ul className="text-ui-sm text-muted-foreground">
                  {scan.skipped.map((skipped) => (
                    <li key={skipped.path}>
                      <span className="font-mono">{skipped.path}</span>: {skipped.reason}
                    </li>
                  ))}
                </ul>
              </section>
            )}
          </>
        )}
      </Loaded>
    </>
  );
}
