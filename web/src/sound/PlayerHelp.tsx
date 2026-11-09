// The player card's small help button, overlaid at the bottom right of the video (above YouTube's own control bar). Playback mostly works, so the "YouTube asking you to sign in?" help lives in a
// pane that hover, focus or a tap opens (a click pins it, Escape or a press outside closes it) instead of taking a line
// of the card. The parent video area is the `group/video` that reveals it. A dot on the icon says playback needs attention, so a real problem is still noticeable.
import { CircleHelp } from "lucide-react";
import { type ReactNode, useEffect, useId, useRef, useState } from "react";
import { AnchoredPopover } from "@/components/AnchoredPopover";
import { PaneActions } from "@/components/PaneActions";
import { t } from "@/i18n";
import { cn } from "@/lib/utils";

const CLOSE_DELAY = 200;

type Props = { attention: boolean; children: ReactNode; actions: ReactNode };

export function PlayerHelp({ attention, children, actions }: Props) {
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const [pinned, setPinned] = useState(false);
  const timer = useRef<number | undefined>(undefined);
  const wrapper = useRef<HTMLSpanElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const pane = useRef<HTMLDivElement>(null);
  const returning = useRef(false);
  const id = useId();
  const open = hovered || focused || pinned;
  useEffect(() => () => window.clearTimeout(timer.current), []);
  useEffect(() => {
    if (!open) return;
    const outside = (event: Event) => {
      const target = event.target as Node;
      if (wrapper.current?.contains(target) || pane.current?.contains(target)) return;
      setPinned(false);
      setHovered(false);
      setFocused(false);
    };
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, [open]);
  const enter = () => {
    window.clearTimeout(timer.current);
    setHovered(true);
  };
  const leave = () => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setHovered(false), CLOSE_DELAY);
  };
  const label = t("sound.playbackHelp");
  return (
    // biome-ignore lint/a11y/noStaticElementInteractions: hover and focus only preview what the button opens on click
    <span
      ref={wrapper}
      className="absolute right-2 bottom-12 z-10 inline-flex"
      onMouseEnter={enter}
      onMouseLeave={leave}
      onFocus={() => {
        if (!returning.current) setFocused(true);
      }}
      onBlur={(event) => {
        const next = event.relatedTarget as Node | null;
        if (!wrapper.current?.contains(next) && !pane.current?.contains(next)) setFocused(false);
      }}
      onKeyDown={(event) => {
        if (event.key !== "Escape" || !open) return;
        event.stopPropagation();
        setHovered(false);
        setFocused(false);
        setPinned(false);
        // Focus goes back to the button without reopening the pane it just closed.
        returning.current = true;
        trigger.current?.focus();
        returning.current = false;
      }}
    >
      <button
        ref={trigger}
        type="button"
        onClick={() => {
          // Hover (or a touch's emulated hover) has already opened it: a click pins it, a second click closes it.
          if (pinned) {
            setPinned(false);
            setHovered(false);
            setFocused(false);
          } else setPinned(true);
        }}
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        aria-label={attention ? `${label} ${t("sound.needsAttention")}` : label}
        title={label}
        className={cn(
          "relative flex size-7 shrink-0 items-center justify-center rounded-full border border-border bg-popover/90 text-icon shadow-sm backdrop-blur-sm transition-opacity hover:bg-accent focus-visible:opacity-100",
          // Hidden until the video is hovered or holds focus; a touch screen has no hover, so it stays faintly visible.
          // A problem, or an open pane, keeps it fully visible.
          attention || open
            ? "opacity-100"
            : "opacity-0 group-focus-within/video:opacity-100 group-hover/video:opacity-100 pointer-coarse:opacity-70",
        )}
      >
        <CircleHelp aria-hidden className="size-3.5" />
        {attention && (
          <span
            data-attention
            aria-hidden
            className="absolute top-1 right-1 size-1.5 rounded-full bg-warn ring-1 ring-popover"
          />
        )}
      </button>
      {open && (
        // biome-ignore lint/a11y/noStaticElementInteractions: the pane shares hover and focus with its button
        <div onMouseEnter={enter} onMouseLeave={leave} onFocus={() => setFocused(true)}>
          <AnchoredPopover
            anchor={trigger}
            contentRef={pane}
            id={id}
            role="region"
            aria-label={label}
            className="w-72 max-w-[calc(100vw-1rem)] p-2 text-ui-sm text-foreground"
          >
            <div className="px-1.5 py-1">{children}</div>
            <PaneActions className="mt-1">{actions}</PaneActions>
          </AnchoredPopover>
        </div>
      )}
    </span>
  );
}
