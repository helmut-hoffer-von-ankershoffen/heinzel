// The presentation contract for built-in and future contributed panels: one selected view, stable mounted contents.
import { type ReactNode, useId, useLayoutEffect, useRef } from "react";
import { t } from "@/i18n";
import { cn } from "@/lib/utils";

export type WorkspacePanel = { id: string; title: string; content: ReactNode };

type Props = { panels: WorkspacePanel[]; active: string; onSelect: (id: string) => void; flowing?: boolean };

export function WorkspacePanels({ panels, active, onSelect, flowing = false }: Props) {
  const prefix = useId();
  const tabs = useRef(new Map<string, HTMLButtonElement>());
  const previous = useRef<string | undefined>(undefined);
  const selected = panels.some((panel) => panel.id === active) ? active : panels[0]?.id;
  useLayoutEffect(() => {
    if (previous.current === selected) return;
    if (selected) {
      tabs.current.get(selected)?.scrollIntoView?.({ block: "nearest", inline: "nearest" });
      if (
        previous.current &&
        !panels.some((panel) => panel.id === previous.current) &&
        document.activeElement === document.body
      )
        tabs.current.get(selected)?.focus();
    }
    previous.current = selected;
  }, [selected, panels]);
  return (
    <>
      {panels.length > 1 && (
        <div
          role="tablist"
          aria-label={t("workspace.panels")}
          className="workspace-tabs flex min-w-0 shrink-0 gap-1 overflow-x-auto border-b border-border px-2 py-1"
        >
          {panels.map((panel, index) => (
            <button
              key={panel.id}
              type="button"
              role="tab"
              ref={(element) => {
                if (element) tabs.current.set(panel.id, element);
                else tabs.current.delete(panel.id);
              }}
              id={`${prefix}-${panel.id}-tab`}
              aria-controls={`${prefix}-${panel.id}`}
              aria-selected={panel.id === selected}
              tabIndex={panel.id === selected ? 0 : -1}
              onClick={() => onSelect(panel.id)}
              onKeyDown={(event) => {
                const next =
                  event.key === "ArrowRight"
                    ? (index + 1) % panels.length
                    : event.key === "ArrowLeft"
                      ? (index + panels.length - 1) % panels.length
                      : event.key === "Home"
                        ? 0
                        : event.key === "End"
                          ? panels.length - 1
                          : null;
                const target = next === null ? undefined : panels[next];
                if (!target) return;
                event.preventDefault();
                onSelect(target.id);
                tabs.current.get(target.id)?.focus();
                tabs.current.get(target.id)?.scrollIntoView?.({ block: "nearest", inline: "nearest" });
              }}
              className={cn(
                "min-h-8 shrink-0 whitespace-nowrap rounded-lg px-3 text-ui-sm hover:bg-accent",
                panel.id === selected ? "bg-accent text-accent-foreground" : "text-muted-foreground",
              )}
            >
              {panel.title}
            </button>
          ))}
        </div>
      )}
      {panels.map((panel) => (
        <div
          key={panel.id}
          role="tabpanel"
          id={`${prefix}-${panel.id}`}
          aria-labelledby={panels.length > 1 ? `${prefix}-${panel.id}-tab` : undefined}
          aria-label={panels.length === 1 ? panel.title : undefined}
          hidden={panel.id !== selected}
          className={cn(
            "min-w-0",
            flowing ? "shrink-0" : "min-h-0 flex-1 overflow-hidden",
            panel.id !== selected && "hidden",
          )}
        >
          {panel.content}
        </div>
      ))}
    </>
  );
}
