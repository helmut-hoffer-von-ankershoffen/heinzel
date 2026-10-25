// One search field: an icon and an input in a single rounded box with one focus ring. The input draws no outline of
// its own (the global :focus-visible one would double the box's); the box shows a ring while the keyboard has the focus
// in it, and not while it is focused for you the moment it opens (`quietFocus`), until you type or return with the keyboard.
import { Search } from "lucide-react";
import { type ComponentPropsWithoutRef, useEffect, useRef, useState } from "react";
import { cn } from "@/lib/utils";

type Props = Omit<ComponentPropsWithoutRef<"input">, "type"> & {
  /** What a screen reader hears for the field. */
  label: string;
  /** Focus the field on mount, without a ring. */
  quietFocus?: boolean;
  inputClassName?: string;
  ref?: React.Ref<HTMLInputElement>;
};

function keyboardFocus(element: HTMLElement): boolean {
  try {
    return element.matches(":focus-visible");
  } catch {
    return true;
  }
}

export function SearchField({
  label,
  quietFocus,
  className,
  inputClassName,
  ref,
  onFocus,
  onBlur,
  onKeyDown,
  ...input
}: Props) {
  const own = useRef<HTMLInputElement | null>(null);
  const [ring, setRing] = useState(false);
  const quiet = useRef(false);
  useEffect(() => {
    if (!quietFocus) return;
    quiet.current = true;
    const element = own.current;
    if (!element) return;
    if (!element.closest("[inert]")) {
      element.focus({ preventScroll: true });
      return;
    }
    // A direct Settings link can mount while startup intentionally makes the app inert. Restore the intended
    // initial focus when that gate clears, rather than losing it or stealing a newer editable field's focus.
    const ready = new MutationObserver(() => {
      if (!element.isConnected) {
        ready.disconnect();
        return;
      }
      if (element.closest("[inert]")) return;
      ready.disconnect();
      const active = document.activeElement;
      if (
        active === element ||
        !(active instanceof HTMLElement) ||
        !active.matches("input,textarea,[contenteditable='true']")
      )
        element.focus({ preventScroll: true });
    });
    for (let parent = element.parentElement; parent; parent = parent.parentElement)
      ready.observe(parent, { attributes: true, attributeFilter: ["inert"] });
    return () => ready.disconnect();
  }, [quietFocus]);
  return (
    <label
      className={cn(
        "flex items-center gap-2 rounded-lg bg-input px-2.5 py-1.5 text-icon ring-foreground/80",
        ring && "ring-1",
        className,
      )}
    >
      <Search aria-hidden className="size-4 shrink-0" />
      <span className="sr-only">{label}</span>
      <input
        {...input}
        ref={(element) => {
          own.current = element;
          if (typeof ref === "function") ref(element);
          else if (ref) ref.current = element;
        }}
        type="search"
        data-focus-ring="parent"
        onKeyDown={(event) => {
          quiet.current = false;
          setRing(true);
          onKeyDown?.(event);
        }}
        onFocus={(event) => {
          setRing(!quiet.current && keyboardFocus(event.currentTarget));
          onFocus?.(event);
        }}
        onBlur={(event) => {
          quiet.current = false;
          setRing(false);
          onBlur?.(event);
        }}
        className={cn(
          "w-full bg-transparent text-ui text-foreground outline-none placeholder:text-muted-foreground",
          inputClassName,
        )}
      />
    </label>
  );
}
