// A setting whose valid values the server knows but that change with what is installed (`x-options` in its schema,
// ADR 0055): the adapters of a capability, the themes, the layers or the modules, offered as a choice; never typed.
import { useConnectors, useLayers, useModules, useThemes } from "@/api/queries";
import { t } from "@/i18n";
import type { OptionsSource } from "./settingSchema";

const field =
  "h-8 max-w-60 rounded-lg border border-border bg-input px-2 text-sm outline-none focus:border-muted-foreground/50 disabled:opacity-50";

export type Option = { value: string; label: string };

type Props = {
  id?: string;
  label: string;
  from: OptionsSource;
  multiple: boolean;
  value: unknown;
  disabled: boolean;
  onChange: (value: unknown) => void;
};

/** The options of a source, or null while they load. */
function useOptions(from: OptionsSource): Option[] | null {
  const adapters = useConnectors(from.source === "adapters");
  const themes = useThemes(from.source === "themes");
  const layers = useLayers(from.source === "layers");
  const modules = useModules(from.source === "modules");
  switch (from.source) {
    case "adapters": {
      const choice = adapters.data?.capabilities.find((entry) => entry.capability === from.capability);
      if (!choice) return adapters.data ? [] : null;
      const found = choice.candidates.map((candidate) => ({
        value: candidate.adapter,
        label:
          candidate.adapter === candidate.connector ? candidate.title : `${candidate.title} (${candidate.adapter})`,
      }));
      return [...found, { value: from.none ?? "none", label: t("bindings.none") }];
    }
    case "themes":
      return themes.data ? themes.data.themes.map((theme) => ({ value: theme.name, label: theme.title })) : null;
    case "layers":
      return layers.data ? layers.data.layers.map((layer) => ({ value: layer.name, label: layer.label })) : null;
    case "modules":
      return modules.data ? modules.data.map((module) => ({ value: module.qualname, label: module.qualname })) : null;
    default:
      return [];
  }
}

export function OptionsValue({ id, label, from, multiple, value, disabled, onChange }: Props) {
  const options = useOptions(from);
  if (options === null) return <span className="text-sm text-muted-foreground">{t("settings.loading")}</span>;
  if (multiple) {
    const chosen = new Set(Array.isArray(value) ? value.map(String) : []);
    return (
      <ul className="flex flex-wrap gap-x-4 gap-y-1">
        {options.map((option) => (
          <li key={option.value}>
            <label className="flex items-center gap-1.5 text-sm">
              <input
                type="checkbox"
                checked={chosen.has(option.value)}
                disabled={disabled}
                onChange={(event) => {
                  const next = new Set(chosen);
                  if (event.target.checked) next.add(option.value);
                  else next.delete(option.value);
                  onChange([...next].sort());
                }}
              />
              {option.label}
            </label>
          </li>
        ))}
      </ul>
    );
  }
  const current = typeof value === "string" ? value : "";
  // A value nobody offers any more (a plugin removed) stays visible, so the choice can be seen and changed.
  const shown =
    current && !options.some((option) => option.value === current)
      ? [...options, { value: current, label: current }]
      : options;
  return (
    <select
      id={id}
      aria-label={id ? undefined : label}
      value={current}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value === "" ? null : event.target.value)}
      className={field}
    >
      <option value="">{t("settings.notSet")}</option>
      {shown.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  );
}
