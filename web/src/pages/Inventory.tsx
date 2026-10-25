// Playbooks' asset types (`hz heinzel playbooks`) and the assets found for them (`hz heinzel discover`).
import { Search } from "lucide-react";
import { useState } from "react";
import { useDiscover, usePlaybooks } from "@/api/queries";
import { Empty, Loaded, Page } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Badge, Card } from "@/components/ui/card";
import { t } from "@/i18n";
import { commandsIn } from "@/lib/utils";

export function AssetTypes() {
  const playbooks = usePlaybooks();
  return (
    <Page title={t("inventory.typesTitle")} intro={t("inventory.typesIntro")}>
      <Loaded query={playbooks}>
        {(data) =>
          data.playbooks.length === 0 ? (
            <Empty
              text={t("inventory.typesEmpty")}
              gnome={{ state: "thinking" }}
              commands={[...data.next.map((step) => step.command), "hz system plugin search"].slice(0, 3)}
            />
          ) : (
            data.playbooks.map((playbook) => (
              <section
                key={playbook.module}
                aria-labelledby={`playbook-${playbook.module}`}
                className="flex flex-col gap-2"
              >
                <h2 id={`playbook-${playbook.module}`} className="font-semibold">
                  {playbook.module}{" "}
                  <span className="text-sm font-normal text-muted-foreground">{playbook.summary}</span>
                </h2>
                <ul className="grid gap-2 md:grid-cols-2">
                  {playbook.assets.map((asset) => (
                    <li key={asset.name} id={encodeURIComponent(asset.name)}>
                      <Card>
                        <p className="font-medium">
                          {asset.name} {asset.kept_by && <Badge>{asset.kept_by}</Badge>}
                        </p>
                        <p className="text-sm text-muted-foreground">{asset.description}</p>
                        {asset.hints.length > 0 && (
                          <ul className="mt-2 flex flex-wrap gap-1">
                            {asset.hints.map((hint) => (
                              <li key={`${hint.read}-${hint.summary}`}>
                                <Badge title={hint.summary}>{hint.read}</Badge>
                              </li>
                            ))}
                          </ul>
                        )}
                      </Card>
                    </li>
                  ))}
                </ul>
              </section>
            ))
          )
        }
      </Loaded>
    </Page>
  );
}

export function Assets() {
  const [asked, setAsked] = useState(false);
  const discovery = useDiscover(asked);
  return (
    <Page
      title={t("inventory.assetsTitle")}
      intro={t("inventory.assetsIntro")}
      actions={
        <Button onClick={() => (asked ? discovery.refetch() : setAsked(true))} disabled={discovery.isFetching}>
          <Search aria-hidden />
          {t("inventory.search")}
        </Button>
      }
    >
      {asked && (
        <Loaded query={discovery}>
          {(data) => (
            <>
              {data.problems.map((problem) => (
                <p
                  key={`${problem.playbook}/${problem.asset}/${problem.hint}`}
                  role="alert"
                  className="text-sm text-warn"
                >
                  {problem.asset} ({problem.hint}): {problem.problem}
                </p>
              ))}
              {data.candidates.length === 0 ? (
                <Empty
                  text={t("inventory.assetsEmpty")}
                  gnome={{ state: "thinking" }}
                  commands={data.next.flatMap((step) => commandsIn(step.command))}
                />
              ) : (
                <ul className="flex flex-col gap-2">
                  {data.candidates.map((candidate) => (
                    <li key={`${candidate.asset}/${candidate.ref}`}>
                      <Card>
                        <p className="font-medium">
                          {candidate.url ? (
                            <a
                              href={candidate.url}
                              className="text-primary underline-offset-2 hover:underline"
                              rel="noreferrer noopener"
                              target="_blank"
                            >
                              {candidate.title}
                            </a>
                          ) : (
                            candidate.title
                          )}{" "}
                          <Badge>{candidate.asset}</Badge>
                        </p>
                        <p className="text-xs text-muted-foreground">
                          {candidate.playbook} · {candidate.hint} · <code className="font-mono">{candidate.ref}</code>
                        </p>
                      </Card>
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
        </Loaded>
      )}
    </Page>
  );
}
