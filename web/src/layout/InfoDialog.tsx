// Changelog and help inside the app, as dialogs over the screen (`?panel=changelog|help`), not pages on GitHub.
import { X } from "lucide-react";
import { useId, useState } from "react";
import { useChangelog, useExpert } from "@/api/queries";
import { CopyCommand, Loaded } from "@/components/common";
import { Prose } from "@/components/Prose";
import { t } from "@/i18n";
import { cn } from "@/lib/utils";

export type Panel = "changelog" | "help";

export function InfoDialog({ panel, onClose }: { panel: Panel; onClose: () => void }) {
  const titleId = useId();
  return (
    // biome-ignore lint/a11y/noStaticElementInteractions: a click on the backdrop closes it; Escape and the close button do for keyboards
    <div
      className="fixed inset-0 z-(--layer-modal) flex items-center justify-center bg-black/50 p-4"
      onMouseDown={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onKeyDown={(event) => event.key === "Escape" && onClose()}
        onMouseDown={(event) => event.stopPropagation()}
        className="flex h-[min(48rem,calc(100vh-2rem))] w-[min(48rem,100%)] flex-col overflow-hidden rounded-xl border border-border bg-popover shadow-2xl"
      >
        <header className="flex h-12 shrink-0 items-center border-b border-border pr-2 pl-5">
          <h2 id={titleId} className="text-ui font-medium">
            {panel === "changelog" ? t("account.changelog") : t("account.help")}
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label={t("settings.close")}
            // biome-ignore lint/a11y/noAutofocus: the dialog takes focus so Escape works at once
            autoFocus
            className="ml-auto flex size-8 items-center justify-center rounded-lg text-muted-foreground hover:bg-accent hover:text-foreground"
          >
            <X aria-hidden className="size-(--icon)" />
          </button>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto px-6 pt-4 pb-10">
          {panel === "changelog" ? <Changelog /> : <Help />}
        </div>
      </div>
    </div>
  );
}

function Changelog() {
  const [newer, setNewer] = useState(false);
  const changelog = useChangelog(newer);
  const tab = (active: boolean) =>
    cn(
      "rounded-lg px-3 py-1.5 text-sm transition-colors hover:bg-accent/70",
      active ? "bg-accent text-foreground" : "text-muted-foreground",
    );
  return (
    <>
      <div role="tablist" className="mb-4 flex gap-1">
        <button type="button" role="tab" aria-selected={!newer} className={tab(!newer)} onClick={() => setNewer(false)}>
          {t("changelog.installed")}
        </button>
        <button type="button" role="tab" aria-selected={newer} className={tab(newer)} onClick={() => setNewer(true)}>
          {t("changelog.newer")}
        </button>
      </div>
      <Loaded query={changelog}>
        {(data) =>
          typeof data === "string" ? (
            <Prose>{data}</Prose>
          ) : data.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t("changelog.upToDate")}</p>
          ) : (
            <>
              {data.map((release) => (
                <section key={release.version} className="mb-6">
                  <h3 className="text-lg font-semibold">
                    {release.version} <span className="text-sm font-normal text-muted-foreground">{release.date}</span>
                  </h3>
                  <Prose>{release.notes}</Prose>
                </section>
              ))}
              <CopyCommand command="hz system update" />
            </>
          )
        }
      </Loaded>
    </>
  );
}

/** `system.doctor:doctor` → `hz system doctor`; `org:show` → `hz org show`. */
export function commandOf(module: string, operation: string): string {
  const words = module.split(".");
  if (words.at(-1) !== operation) words.push(operation);
  return `hz ${words.join(" ")}`;
}

function Help() {
  const expert = useExpert();
  return (
    <>
      <Prose>{t("help.intro")}</Prose>
      <h3 className="mt-6 mb-2 text-ui font-medium">{t("help.commands")}</h3>
      <p className="mb-3 text-sm text-muted-foreground">{t("help.commandsIntro")}</p>
      <Loaded query={expert}>
        {(mode) => (
          <ul className="flex flex-col gap-2">
            {mode.operations
              .filter((item) => mode.expert || item.audience === "everyday")
              .map((item) => (
                <li key={`${item.module}:${item.operation}`}>
                  <CopyCommand command={commandOf(item.module, item.operation)} />
                </li>
              ))}
          </ul>
        )}
      </Loaded>
      <h3 className="mt-6 mb-2 text-ui font-medium">{t("help.stuck")}</h3>
      <Prose>{t("help.stuckBody")}</Prose>
    </>
  );
}
