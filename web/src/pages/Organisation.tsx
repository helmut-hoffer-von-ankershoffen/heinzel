// The org chart (the directory capability, once an operation reads it) and the team roster (`hz team show`).
import { useTeam } from "@/api/queries";
import { Empty, Loaded, Page } from "@/components/common";
import { Badge } from "@/components/ui/card";
import { format, t } from "@/i18n";
import { commandsIn } from "@/lib/utils";

/** No operation reads the directory yet (see TeamService.draft), so the chart shows how it will come. */
export function OrgChart() {
  return (
    <Page title={t("org.title")} intro={t("org.intro")}>
      <Empty text={t("org.notYet")} commands={["hz system capability list"]} />
    </Page>
  );
}

export function Team() {
  const team = useTeam();
  return (
    <Page title={t("team.title")}>
      <Loaded query={team}>
        {(data) =>
          !data.exists ? (
            <Empty
              text={t("team.none")}
              commands={
                data.next?.length ? data.next.flatMap(commandsIn) : ["hz team join <url>", "hz team new <name>"]
              }
            />
          ) : (
            <>
              <p className="text-sm">
                <span className="font-semibold">{data.name}</span>{" "}
                {data.repository && <code className="font-mono text-xs text-muted-foreground">{data.repository}</code>}
              </p>
              {data.problems?.map((problem) => (
                <p key={problem} role="alert" className="text-sm text-warn">
                  {problem}
                </p>
              ))}
              <div className="overflow-x-auto rounded-lg border border-border">
                <table className="w-full text-left text-sm">
                  <thead className="bg-muted text-xs text-muted-foreground">
                    <tr>
                      <th scope="col" className="p-2">
                        {t("team.handle")}
                      </th>
                      <th scope="col" className="p-2">
                        {t("team.github")}
                      </th>
                      <th scope="col" className="p-2">
                        {t("team.roles")}
                      </th>
                      <th scope="col" className="p-2">
                        {t("team.since")}
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {(data.members ?? []).map((member) => (
                      <tr key={member.handle} className="border-t border-border">
                        <td className="p-2 font-medium">
                          {member.handle} {member.owner && <Badge tone="primary">{t("team.owner")}</Badge>}
                        </td>
                        <td className="p-2">{member.github}</td>
                        <td className="p-2">{member.roles.join(", ")}</td>
                        <td className="p-2">{format.date(member.since)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )
        }
      </Loaded>
    </Page>
  );
}
