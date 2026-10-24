// The colourful Heinzel the app greets you with, wherever the app means "your Heinzelmännchen" (the chat button, the
// chat's header, the fallback logo): Heinzelmännchen's one icon, the favicon, so it looks the same on every system
// rather than like each one's emoji font. Sized by the font size it sits in, like the emoji it replaced.
import { cn } from "@/lib/utils";

export function HeinzelIcon({ className }: { className?: string }) {
  return (
    <span
      aria-hidden
      data-testid="heinzel-icon"
      className={cn("inline-flex shrink-0 items-center justify-center leading-none", className)}
    >
      <img src="/favicon.svg" alt="" draggable={false} className="size-[1.25em]" />
    </span>
  );
}
