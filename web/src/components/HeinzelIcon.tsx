// The colourful Heinzel the app greets you with, wherever the app means "your Heinzelmännchen" (the chat button, the
// fallback logo): the classic one, as the app's icon shows it, the same on every system rather than each one's emoji.
// Sized by the font size it sits in, like the emoji it replaced.
import { cn } from "@/lib/utils";
import { HeinzelAvatar } from "./HeinzelAvatar";

export function HeinzelIcon({ className }: { className?: string }) {
  return (
    <span
      aria-hidden
      data-testid="heinzel-icon"
      className={cn("inline-flex shrink-0 items-center justify-center leading-none", className)}
    >
      <HeinzelAvatar />
    </span>
  );
}
