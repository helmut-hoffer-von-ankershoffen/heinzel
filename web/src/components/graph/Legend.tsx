// What a graph's shapes mean: one swatch per kind of node, never colour alone (the icon and the word say it too).
import type { LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

export type LegendItem = { label: string; icon: LucideIcon; className: string };

export function Legend({ title, items }: { title: string; items: LegendItem[] }) {
  return (
    <figure className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-muted-foreground">
      <figcaption className="sr-only">{title}</figcaption>
      <ul className="flex flex-wrap items-center gap-x-4 gap-y-2" aria-label={title}>
        {items.map((item) => {
          const Icon = item.icon;
          return (
            <li key={item.label} className="flex items-center gap-1.5">
              <span
                className={cn("flex size-5 items-center justify-center rounded-md border", item.className)}
                aria-hidden
              >
                <Icon className="size-3" />
              </span>
              {item.label}
            </li>
          );
        })}
      </ul>
    </figure>
  );
}
