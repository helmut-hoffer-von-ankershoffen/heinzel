// `hz system config show`: every setting of every module, grouped by section, with where its value comes from.
import { useId } from "react";
import { useSearchParams } from "react-router";
import type { SchemaSettingInfo } from "@/api/client";
import { useConfig, useExpert, useSetExpert } from "@/api/queries";
import { CopyCommand, Loaded, Page } from "@/components/common";
import { Badge, Card } from "@/components/ui/card";
import { t } from "@/i18n";
import { cn, configValue } from "@/lib/utils";

function shown(value: unknown): string {
  if (value === null || value === undefined) return t("settings.notSet");
  return typeof value === "string" ? value : JSON.stringify(value);
}

const sourceTone = { default: "neutral", env: "warn" } as const;

function Setting({ setting }: { setting: SchemaSettingInfo }) {
  const key = `${setting.section}.${setting.key}`;
  const changed = setting.source !== "default";
  return (
    <tr className="border-t border-border align-top">
      <th scope="row" className="p-2 text-left font-normal">
        <code className="font-mono text-xs font-semibold">{setting.key}</code>
        <p className="mt-1 max-w-md text-xs text-muted-foreground">{setting.description}</p>
        <p className="mt-1 font-mono text-[11px] text-muted-foreground">{setting.env}</p>
      </th>
      <td className="max-w-xs p-2 font-mono text-xs break-all">{shown(setting.value)}</td>
      <td className="max-w-xs p-2 font-mono text-xs break-all text-muted-foreground">{shown(setting.default)}</td>
      <td className="p-2">
        <Badge tone={sourceTone[setting.source as keyof typeof sourceTone] ?? "primary"}>{setting.source}</Badge>
      </td>
      <td className="p-2">
        <CopyCommand
          command={`hz system config set ${key} ${setting.value === null ? "VALUE" : configValue(setting.value)}`}
        />
        {changed && setting.source !== "env" && (
          <div className="mt-1">
            <CopyCommand command={`hz system config unset ${key}`} />
          </div>
        )}
      </td>
    </tr>
  );
}

/** `hz system expert`: a switch that runs `hz system expert on` or `off` through the API (ADR 0041). */
function ExpertSwitch() {
  const expert = useExpert();
  const change = useSetExpert();
  const labelId = useId();
  const on = expert.data?.expert ?? false;
  return (
    <Card>
      <div className="flex items-center gap-3">
        <button
          type="button"
          role="switch"
          aria-checked={on}
          aria-labelledby={labelId}
          disabled={!expert.data || change.isPending}
          onClick={() => change.mutate(!on)}
          className={cn(
            "relative h-6 w-11 shrink-0 rounded-full border border-border transition-colors disabled:opacity-50",
            on ? "bg-primary" : "bg-muted",
          )}
        >
          <span
            aria-hidden
            className={cn(
              "absolute top-0.5 left-0.5 size-4 rounded-full bg-background shadow transition-transform",
              on && "translate-x-5",
            )}
          />
        </button>
        <h2 id={labelId} className="text-sm font-semibold">
          {t("settings.expert")}
        </h2>
        <CopyCommand command={on ? "hz system expert off" : "hz system expert on"} />
      </div>
      <p className="mt-2 text-xs text-muted-foreground">{on ? t("settings.expertOn") : t("settings.expertOff")}</p>
      {expert.data?.note && (
        <p role="alert" className="mt-1 text-xs text-warn">
          {expert.data.note}
        </p>
      )}
      {change.error && (
        <p role="alert" className="mt-1 text-xs text-bad">
          {t("settings.expertFailed", { problem: change.error.message })}
        </p>
      )}
    </Card>
  );
}

export function Settings() {
  const config = useConfig();
  const [params, setParams] = useSearchParams();
  const filter = params.get("q") ?? "";
  const filterId = useId();
  return (
    <Page title={t("settings.title")} intro={t("settings.intro")}>
      <ExpertSwitch />
      <Loaded query={config}>
        {(report) => {
          const needle = filter.toLowerCase();
          const visible = report.settings.filter((setting) =>
            `${setting.section}.${setting.key} ${setting.description}`.toLowerCase().includes(needle),
          );
          const sections = [...new Set(visible.map((setting) => setting.section))];
          return (
            <>
              <Card>
                <h2 className="mb-2 text-sm font-semibold">{t("settings.files")}</h2>
                <ul className="flex flex-col gap-1 text-xs">
                  {report.files.map((file) => (
                    <li key={file.path} className="flex gap-2">
                      <Badge>{file.scope}</Badge>
                      <code className="font-mono break-all">{file.path}</code>
                      <span className="text-muted-foreground">
                        {file.exists ? t("settings.exists") : t("settings.missing")}
                      </span>
                    </li>
                  ))}
                </ul>
              </Card>
              <label htmlFor={filterId} className="text-sm font-medium">
                {t("settings.filter")}
              </label>
              <input
                id={filterId}
                type="search"
                value={filter}
                onChange={(event) => setParams(event.target.value ? { q: event.target.value } : {}, { replace: true })}
                className="h-9 w-full max-w-sm rounded-md border border-border bg-background px-2 text-sm"
              />
              {sections.map((section) => (
                <section key={section} aria-labelledby={`section-${section}`} className="flex flex-col gap-2">
                  <h2 id={`section-${section}`} className="font-mono text-sm font-semibold">
                    [{section}]
                  </h2>
                  <div className="overflow-x-auto rounded-lg border border-border">
                    <table className="w-full text-sm">
                      <thead className="bg-muted text-left text-xs text-muted-foreground">
                        <tr>
                          <th scope="col" className="p-2">
                            {t("settings.key")}
                          </th>
                          <th scope="col" className="p-2">
                            {t("settings.value")}
                          </th>
                          <th scope="col" className="p-2">
                            {t("settings.default")}
                          </th>
                          <th scope="col" className="p-2">
                            {t("settings.source")}
                          </th>
                          <th scope="col" className="p-2">
                            <span className="sr-only">{t("copy.label")}</span>
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        {visible
                          .filter((setting) => setting.section === section)
                          .map((setting) => (
                            <Setting key={setting.key} setting={setting} />
                          ))}
                      </tbody>
                    </table>
                  </div>
                </section>
              ))}
            </>
          );
        }}
      </Loaded>
    </Page>
  );
}
