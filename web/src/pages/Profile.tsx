// `hz org show`: your profile, and the command that completes or changes it.
import { Link } from "react-router";
import { usePlayful, useProfile } from "@/api/queries";
import { CopyCommand, Loaded, Page } from "@/components/common";
import { Gnome } from "@/components/Gnome";
import { Card } from "@/components/ui/card";
import { type MessageKey, t } from "@/i18n";

export function Profile() {
  const profile = useProfile();
  return (
    <Page title={t("profile.title")} intro={t("profile.intro")}>
      <Loaded query={profile}>
        {(me) => {
          const rows: [MessageKey, string | null | undefined, string][] = [
            ["profile.handle", me.handle, "org.handle"],
            ["profile.role", me.role, "org.role"],
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
                      {value ?? (
                        <Link to={`?settings=&q=${key}`} className="text-muted-foreground underline">
                          {t("settings.notSet")}
                        </Link>
                      )}
                    </dd>
                  </div>
                ))}
              </dl>
              <div className="mt-4">
                <CopyCommand command={me.fix} />
              </div>
            </Card>
          );
        }}
      </Loaded>
    </Page>
  );
}

export function NotFound() {
  const playful = usePlayful();
  return (
    <Page title={t("notFound.title")}>
      <div className="flex items-end gap-4">
        <Gnome state="waiting" age="grown" />
        <div className="flex flex-col gap-1 pb-2">
          {playful && (
            <p className="text-sm text-muted-foreground" aria-hidden>
              {t("notFound.guide")}
            </p>
          )}
          <Link to="/" className="text-primary underline">
            {t("notFound.back")}
          </Link>
        </div>
      </div>
    </Page>
  );
}
