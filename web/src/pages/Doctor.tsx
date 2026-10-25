// `hz system doctor`: every module's checks, each problem with the command that fixes it.
import { RefreshCw } from "lucide-react";
import { useDoctor } from "@/api/queries";
import { CopyCommand, Loaded, Page, StatusBadge } from "@/components/common";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n";
import { commandsIn } from "@/lib/utils";

export function Doctor() {
  const doctor = useDoctor();
  return (
    <Page
      title={t("doctor.title")}
      intro={t("doctor.intro")}
      actions={
        <Button variant="outline" onClick={() => doctor.refetch()} disabled={doctor.isFetching}>
          <RefreshCw aria-hidden />
          {t("doctor.rerun")}
        </Button>
      }
    >
      <Loaded query={doctor}>
        {(report) => (
          <>
            <p className="flex items-center gap-2 text-lg">
              {t("doctor.status")}
              <StatusBadge status={report.status} />
            </p>
            <div className="overflow-x-auto rounded-lg border border-border">
              <table className="w-full text-left text-sm">
                <caption className="sr-only">{t("doctor.title")}</caption>
                <thead className="bg-muted text-xs text-muted-foreground">
                  <tr>
                    <th scope="col" className="p-2">
                      {t("doctor.module")}
                    </th>
                    <th scope="col" className="p-2">
                      {t("doctor.check")}
                    </th>
                    <th scope="col" className="p-2">
                      {t("doctor.statusColumn")}
                    </th>
                    <th scope="col" className="p-2">
                      {t("doctor.detail")}
                    </th>
                    <th scope="col" className="p-2">
                      {t("doctor.fix")}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(report.checks).flatMap(([module, checks]) =>
                    checks.map((check) => (
                      <tr key={`${module}/${check.name}`} className="border-t border-border align-top">
                        <td className="p-2 font-mono text-xs">{module}</td>
                        <td className="p-2">{check.name}</td>
                        <td className="p-2">
                          <StatusBadge status={check.status} />
                        </td>
                        <td className="p-2 break-words text-muted-foreground">{check.detail}</td>
                        <td className="p-2">
                          {check.status !== "up" && check.fix && (
                            <div className="flex flex-col items-start gap-1">
                              <span className="text-xs text-muted-foreground">{check.fix}</span>
                              {commandsIn(check.fix).map((command) => (
                                <CopyCommand key={command} command={command} />
                              ))}
                            </div>
                          )}
                        </td>
                      </tr>
                    )),
                  )}
                </tbody>
              </table>
            </div>
          </>
        )}
      </Loaded>
    </Page>
  );
}
