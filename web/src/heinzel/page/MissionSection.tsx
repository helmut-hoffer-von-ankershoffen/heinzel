// The mission on the Heinzel's page (canvas board Profile): what it does alone, what it asks first and what it never
// does, and the version of its terms. Every line is a fact the code enforces (no tool without approval, nothing others
// see without approval, the spend limit of the terms), never a promise read out of the terms' prose.
import { useQuery } from "@tanstack/react-query";
import { CircleCheck, CircleHelp, CircleX } from "lucide-react";
import { useId } from "react";
import { api, problem } from "@/api/client";
import type { components } from "@/api/schema";
import { t } from "@/i18n";

type Snapshot = components["schemas"]["SessionSnapshot"];

/** The latest proposed terms; the same read (and cache entry) as the terms review in Reviews and records. */
export function useAgentProposal(sessionId: string) {
  return useQuery({
    queryKey: ["heinzel", "agent-proposal", sessionId],
    queryFn: async () => {
      const { data, error, response } = await api.GET("/heinzel/latest-scheduled-agent-proposal", {
        params: { query: { session_id: sessionId } },
      });
      if (!response.ok || data === undefined) throw problem(response.status, error);
      if (data && (data.session_id !== sessionId || data.status !== "proposed")) throw new Error("Invalid proposal");
      return data;
    },
    retry: false,
  });
}

function Group({ icon: Icon, title, items }: { icon: typeof CircleCheck; title: string; items: string[] }) {
  const id = useId();
  return (
    <div className="flex flex-col gap-1">
      <h3 id={id} className="flex items-center gap-1.5 text-sm font-medium">
        <Icon aria-hidden className="size-4 text-icon" />
        {title}
      </h3>
      <ul aria-labelledby={id} className="list-disc pl-6 text-sm">
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </div>
  );
}

export function MissionSection({ snapshot }: { snapshot: Snapshot }) {
  const heading = useId();
  const proposal = useAgentProposal(snapshot.session_id).data;
  const contracts = snapshot.contracts ?? [];
  const approved = [...contracts].reverse().find((item) => item.status === "approved");
  const latest = approved ?? contracts.at(-1);
  const schedule = snapshot.schedule;
  const alone = [t("page.alone.chat")];
  if (schedule?.status === "active") alone.push(t("page.alone.schedule", { summary: schedule.summary }));
  const never = [t("page.never.tools")];
  if (typeof proposal?.max_usd === "number") never.push(t("page.never.spend", { usd: `$${proposal.max_usd}` }));
  const version = snapshot.contract_version
    ? t("page.version", { version: String(snapshot.contract_version) })
    : proposal
      ? t("page.versionProposed", { version: String(proposal.revision) })
      : null;
  const text = approved?.text ?? proposal?.contract ?? latest?.text;
  return (
    <section aria-labelledby={heading} className="flex flex-col gap-3">
      <h2 id={heading} className="text-base font-semibold">
        {t("page.mission")}
      </h2>
      {latest?.title && <p className="text-sm">{latest.title}</p>}
      {version ? (
        <p className="text-sm text-muted-foreground">{version}</p>
      ) : (
        <p className="text-sm text-muted-foreground">{t("page.missionNone", { name: snapshot.name })}</p>
      )}
      <div className="grid gap-3 sm:grid-cols-3">
        <Group icon={CircleCheck} title={t("page.alone")} items={alone} />
        <Group icon={CircleHelp} title={t("page.asks")} items={[t("page.asks.others"), t("page.asks.schedule")]} />
        <Group icon={CircleX} title={t("page.never")} items={never} />
      </div>
      {text && (
        <details className="rounded-lg border border-border p-3 text-sm">
          <summary className="min-h-11 cursor-pointer content-center font-medium">{t("page.terms")}</summary>
          <p className="mt-2 whitespace-pre-wrap break-words">{text}</p>
        </details>
      )}
    </section>
  );
}
