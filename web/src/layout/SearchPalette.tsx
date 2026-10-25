// ⌘K: one search over the navigation, every setting, every contract and every asset type. A native <dialog>
// (focus trap, Escape, inert background) rather than a library one: those inject <style>, which the CSP refuses.
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router";
import { useConfig, useContracts, useExpert, usePlaybooks } from "@/api/queries";
import { t } from "@/i18n";
import { cn } from "@/lib/utils";
import { contractPath, listed, sections, visibleSections } from "./navigation";

type Entry = { label: string; hint: string; to: string };

function useEntries(): Entry[] {
  const config = useConfig();
  const contracts = useContracts();
  const playbooks = usePlaybooks();
  const expert = useExpert();
  return useMemo(() => {
    const entries: Entry[] = [];
    // The same screens the navigation lists: outside expert mode, the everyday ones (ADR 0041).
    for (const section of visibleSections(sections, expert.data)) {
      if (section.to) entries.push({ label: t(section.label), hint: "", to: section.to });
      for (const item of section.items) entries.push({ label: t(item.label), hint: t(section.label), to: item.to });
    }
    entries.push({ label: t("nav.profile"), hint: "", to: "/profile" });
    for (const contract of contracts.data?.heinzelmaennchen ?? []) {
      const qualified = `${contract.namespace}:${contract.name}`;
      entries.push({ label: contract.title ?? contract.name, hint: qualified, to: contractPath(qualified) });
    }
    for (const setting of config.data?.settings ?? []) {
      const key = `${setting.section}.${setting.key}`;
      entries.push({ label: key, hint: setting.description, to: `?settings=&q=${encodeURIComponent(key)}` });
    }
    const assetTypes = listed("heinzel:playbooks", expert.data) ? (playbooks.data?.playbooks ?? []) : [];
    for (const playbook of assetTypes) {
      for (const asset of playbook.assets) {
        entries.push({
          label: asset.name,
          hint: asset.description,
          to: `/inventory/types#${encodeURIComponent(asset.name)}`,
        });
      }
    }
    return entries;
  }, [config.data, contracts.data, playbooks.data, expert.data]);
}

export function SearchPalette({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const navigate = useNavigate();
  const entries = useEntries();
  const needle = query.trim().toLowerCase();
  const matches = (
    needle ? entries.filter((entry) => `${entry.label} ${entry.hint}`.toLowerCase().includes(needle)) : entries
  ).slice(0, 50);
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const element = dialog.current;
    if (!element) return;
    if (open && !element.open) element.showModal?.();
    if (!open && element.open) element.close?.();
  }, [open]);
  const go = (entry: Entry | undefined) => {
    if (!entry) return;
    onOpenChange(false);
    setQuery("");
    navigate(entry.to);
  };
  return (
    <dialog
      ref={dialog}
      aria-label={t("search.label")}
      onClose={() => onOpenChange(false)}
      onCancel={() => onOpenChange(false)}
      // A click on the backdrop lands on the <dialog> itself, outside the box it draws: that closes it.
      onMouseDown={(event) => {
        if (event.target !== event.currentTarget) return;
        const box = event.currentTarget.getBoundingClientRect();
        const inside =
          event.clientX >= box.left &&
          event.clientX <= box.right &&
          event.clientY >= box.top &&
          event.clientY <= box.bottom;
        if (!inside || box.width === 0) onOpenChange(false);
      }}
      className="top-[15%] mx-auto w-[min(36rem,92vw)] overflow-hidden rounded-xl border border-border bg-popover p-0 text-foreground shadow-2xl backdrop:bg-black/40"
    >
      {open && (
        <>
          <input
            autoFocus
            role="combobox"
            aria-expanded
            aria-controls="search-results"
            aria-activedescendant={matches[active] ? `search-${active}` : undefined}
            aria-label={t("search.label")}
            placeholder={t("search.placeholder")}
            className="w-full border-b border-border bg-transparent px-4 py-3 outline-none focus-visible:shadow-[inset_0_-2px_0_var(--color-ring)] focus-visible:outline-none"
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              setActive(0);
            }}
            onKeyDown={(event) => {
              if (event.key === "ArrowDown") setActive((index) => Math.min(index + 1, matches.length - 1));
              if (event.key === "ArrowUp") setActive((index) => Math.max(index - 1, 0));
              if (event.key === "Enter") go(matches[active]);
            }}
          />
          <div
            id="search-results"
            role="listbox"
            aria-label={t("search.label")}
            className="max-h-80 overflow-x-hidden overflow-y-auto p-1"
          >
            {matches.length === 0 && <p className="p-3 text-sm text-muted-foreground">{t("search.none")}</p>}
            {matches.map((entry, index) => (
              <div
                key={`${entry.to}-${entry.label}`}
                id={`search-${index}`}
                role="option"
                tabIndex={-1}
                aria-selected={index === active}
                className={cn(
                  "flex min-w-0 cursor-pointer items-baseline rounded-lg px-3 py-2 text-sm",
                  index === active && "bg-accent",
                )}
                onMouseEnter={() => setActive(index)}
                onClick={() => go(entry)}
                onKeyDown={() => undefined}
              >
                <span className="shrink-0 font-medium">{entry.label}</span>
                {entry.hint && (
                  <span className="ml-2 min-w-0 truncate text-xs text-muted-foreground">{entry.hint}</span>
                )}
              </div>
            ))}
          </div>
        </>
      )}
    </dialog>
  );
}
