import type { components } from "@/api/schema";

type ToolActivity = components["schemas"]["ToolActivity"];

/** Render only host-recorded metadata, in journal order. */
export function ToolEvents({ items, label }: { items: ToolActivity[]; label: string }) {
  return (
    <details className="min-w-0 rounded border border-border p-2 text-sm">
      <summary className="cursor-pointer">
        {label} · {items.length} events
      </summary>
      <ol className="space-y-1">
        {[...items]
          .sort((a, b) => a.seq - b.seq)
          .map((item) => (
            <li key={item.seq} className="break-words">
              <span>
                {item.tool} · {item.status}
              </span>{" "}
              <time dateTime={item.at} className="text-xs text-muted-foreground">
                {new Date(item.at).toLocaleString()}
              </time>
            </li>
          ))}
      </ol>
    </details>
  );
}
