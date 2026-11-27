// `hz org show`: your profile, with links to its settings.
import { Link, useNavigate } from "react-router";
import { useProfile, useSetting } from "@/api/queries";
import { Loaded, Page } from "@/components/common";
import { ErrorScreen } from "@/components/ErrorScreen";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { type MessageKey, t } from "@/i18n";

export function Profile() {
  const profile = useProfile();
  const team = useSetting("team", "name");
  const navigate = useNavigate();
  return (
    <Page title={t("profile.title")} intro={t("profile.intro")}>
      <Loaded query={profile}>
        {(me) => {
          const rows: [MessageKey, string | null | undefined, string][] = [
            ["profile.handle", me.handle, "org.handle"],
            ["profile.role", me.role, "org.role"],
            ["profile.team", team || null, "team.name"],
            ["profile.timezone", me.timezone, "org.timezone"],
            ["profile.hours", me.working_hours, "org.working_hours"],
            ["profile.github", me.github, "org.github"],
            ["profile.agent", me.coding_agent, "org.coding_agent"],
            ["profile.name", me.name, "org.name"],
            ["profile.email", me.email, "org.email"],
          ];
          return (
            <Card>
              <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-sm">
                {rows.map(([label, value, key]) => (
                  <div key={label} className="contents">
                    <dt className="text-muted-foreground">{t(label)}</dt>
                    <dd>
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        className={value ? "-ml-2.5" : "-ml-2.5 text-muted-foreground"}
                        aria-label={t("profile.editField", { field: t(label), value: value || t("settings.notSet") })}
                        onClick={() => navigate(`?settings=${key === "team.name" ? "team" : "you"}&q=${key}`)}
                      >
                        {value || t("settings.notSet")}
                      </Button>
                    </dd>
                  </div>
                ))}
              </dl>
              {me.retired_role && (
                <output className="mt-3 block text-sm text-warn">
                  {t("profile.retiredRole", { role: me.retired_role })}
                </output>
              )}
              <div className="mt-4">
                <Button variant="outline" onClick={() => navigate("?settings=you")}>
                  {t("profile.edit")}
                </Button>
              </div>
            </Card>
          );
        }}
      </Loaded>
    </Page>
  );
}

export function NotFound() {
  return (
    <Page title={t("notFound.title")}>
      <ErrorScreen compact state="waiting" message={t("notFound.message")}>
        <Button asChild variant="outline" size="sm">
          <Link to="/">{t("notFound.back")}</Link>
        </Button>
      </ErrorScreen>
    </Page>
  );
}
