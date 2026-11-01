import type { ComponentProps } from "react";
import { cn } from "@/lib/utils";

/** A one-line monospace field for something pasted or typed exactly: a link, a plugin name or git address. */
export function CodeInput({ className, ...props }: ComponentProps<"input">) {
  return (
    <input
      autoComplete="off"
      spellCheck={false}
      className={cn(
        "h-8 min-w-0 flex-1 rounded-lg border border-border bg-input px-2.5 font-mono text-xs outline-none focus:border-muted-foreground/50",
        className,
      )}
      {...props}
    />
  );
}
