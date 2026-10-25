// Settings → Customize → Connectors: the plugins that connect your tools (they bring adapters for capabilities such
// as tracker or documents), as cards with what each connects and what you use it for; one opens its settings here.
// Under the cards, which connector serves what (`[capabilities]`).
import { ChevronLeft, Plug } from "lucide-react";
import type { ReactNode } from "react";
import type { SchemaSettingInfo } from "@/api/client";
import { useCapabilities } from "@/api/queries";
import { t } from "@/i18n";
import { BINDINGS, group, type SettingsNav } from "./settingsSections";

type Connector = SettingsNav["connectors"][number];

/** `github:tracker` → `tracker`: what an adapter connects. */
const connects = (connector: Connector) => [
  ...new Set((connector.plugin.capabilities ?? []).map((capability) => capability.split(":").at(-1) ?? capability)),
];

type Props = {
  connectors: Connector[];
  open: Connector | undefined;
  settings: SchemaSettingInfo[];
  onOpen: (id: string) => void;
  renderSettings: (settings: SchemaSettingInfo[], heading: string) => ReactNode;
};

export function ConnectorsSection({ connectors, open, settings, onOpen, renderSettings }: Props) {
  const capabilities = useCapabilities();
  /** The capabilities bound to one of `connector`'s adapters, from `[capabilities]`. */
  const used = (connector: Connector) => {
    const adapters = new Set((connector.plugin.capabilities ?? []).map((capability) => capability.split(":")[0]));
    return (capabilities.data?.capabilities ?? [])
      .filter((capability) => capability.bound && adapters.has(capability.bound))
      .map((capability) => capability.name);
  };
  if (open) {
    const own = settings.filter((setting) => open.groups.includes(group(setting.section)));
    const bound = used(open);
    return (
      <>
        <button
          type="button"
          onClick={() => onOpen("connectors")}
          className="mb-3 -ml-1.5 flex items-center gap-1 rounded-lg px-1.5 py-1 text-ui-sm text-muted-foreground hover:bg-accent/70 hover:text-foreground"
        >
          <ChevronLeft aria-hidden className="size-4 text-icon" />
          {t("connectors.title")}
        </button>
        <p className="mb-1 text-sm text-muted-foreground">{open.plugin.summary}</p>
        <p className="mb-4 text-sm text-muted-foreground">
          {t("connectors.connects", { what: connects(open).join(", ") })}
          {" · "}
          {bound.length ? t("connectors.usedFor", { what: bound.join(", ") }) : t("connectors.unused")}
        </p>
        {own.length ? (
          renderSettings(own, open.title)
        ) : (
          <>
            <h3 className="mb-2 text-ui font-medium">{open.title}</h3>
            <p className="text-sm text-muted-foreground">{t("connectors.noSettings")}</p>
          </>
        )}
      </>
    );
  }
  const bindings = settings.filter((setting) => group(setting.section) === BINDINGS);
  return (
    <>
      <h3 className="mb-1 text-ui font-medium">{t("connectors.title")}</h3>
      <p className="mb-4 text-sm text-muted-foreground">{t("connectors.intro")}</p>
      {connectors.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("connectors.none")}</p>
      ) : (
        <ul className="grid grid-cols-[repeat(auto-fill,minmax(15rem,1fr))] gap-3">
          {connectors.map((connector) => {
            const bound = used(connector);
            return (
              <li key={connector.id}>
                <button
                  type="button"
                  onClick={() => onOpen(connector.id)}
                  className="flex h-full w-full flex-col gap-1.5 rounded-xl border border-border bg-card p-3 text-left transition-colors hover:bg-accent/40"
                >
                  <span className="flex items-center gap-2">
                    <Plug aria-hidden className="size-(--icon) shrink-0 text-icon" />
                    <span className="truncate text-ui font-medium">{connector.title}</span>
                    {!connector.plugin.enabled && (
                      <span className="ml-auto shrink-0 text-ui-sm text-muted-foreground">{t("plugins.disabled")}</span>
                    )}
                  </span>
                  <span className="line-clamp-2 text-ui-sm text-muted-foreground">{connector.plugin.summary}</span>
                  <span className="mt-auto text-ui-sm">
                    {t("connectors.connects", { what: connects(connector).join(", ") })}
                  </span>
                  <span className={bound.length ? "text-ui-sm text-ok" : "text-ui-sm text-muted-foreground"}>
                    {bound.length ? t("connectors.usedFor", { what: bound.join(", ") }) : t("connectors.unused")}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
      {bindings.length > 0 && <div className="mt-8">{renderSettings(bindings, t("connectors.bindings"))}</div>}
    </>
  );
}
