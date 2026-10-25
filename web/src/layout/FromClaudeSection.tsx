// Settings → This computer → From Claude: the MCP servers and skills already set up for Claude Code and Claude Desktop
// on this computer, read-only (ADR 0062). Nothing is switched on from here yet; secret values are never read, so a
// server shows the names of the variables and headers it is given, not what they hold.
import { useQuery } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import type { ReactNode } from "react";
import { api, type SchemaClaudeScan, unwrap } from "@/api/client";
import { Loaded } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/card";
import { type MessageKey, t } from "@/i18n";

export const FROM_CLAUDE = "from-claude";

type Server = SchemaClaudeScan["servers"][number];
type Skill = SchemaClaudeScan["skills"][number];

const useClaude = () =>
  useQuery({ queryKey: ["system", "claude", "list"], queryFn: () => unwrap(api.GET("/system/claude/list")) });

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

function ServerRow({ server }: { server: Server }) {
  const via = server.host ?? `${server.command ?? "?"} (${server.arguments})`;
  const given = [...server.env_keys, ...server.header_keys];
  return (
    <>
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium text-ui">{server.name}</span>
        <Badge>{server.transport}</Badge>
        <Badge>{server.auth}</Badge>
        {server.status === "self" && <Badge tone="ok">{t("fromClaude.self")}</Badge>}
      </div>
      <p className="font-mono text-ui-sm text-muted-foreground">{via}</p>
      {given.length > 0 && (
        <p className="text-ui-sm text-muted-foreground">{t("fromClaude.gives", { names: given.join(", ") })}</p>
      )}
    </>
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
            <h4 className="mb-2 text-ui font-medium">{t("fromClaude.servers")}</h4>
            {scan.servers.length === 0 ? (
              <p className="mb-4 text-sm text-muted-foreground">{t("fromClaude.noServers")}</p>
            ) : (
              <Grouped items={scan.servers} row={(server) => <ServerRow server={server} />} />
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
