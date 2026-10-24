// The colourful Heinzel the app greets you with: the same artwork as the status bar's name, wherever the app means
// "your Heinzelmännchen" (the chat button, the chat's header, the fallback logo).
import { cn } from "@/lib/utils";

export function HeinzelIcon({ className }: { className?: string }) {
  return (
    <span aria-hidden className={cn("inline-flex shrink-0 items-center justify-center leading-none", className)}>
      🧝
    </span>
  );
}
