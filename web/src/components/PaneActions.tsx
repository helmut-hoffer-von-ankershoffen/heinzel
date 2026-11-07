// Pane actions (design/README.md, "Pane actions"): content, one divider, then one unwrapped row of icon-only
// buttons whose words are their name and tooltip; words beside the icon only with a stated reason (`withText`).
import type { LucideIcon } from "lucide-react";
import { Children, type ComponentProps, cloneElement, Fragment, isValidElement, type ReactNode } from "react";
import { Link } from "react-router";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n";
import { cn } from "@/lib/utils";

/** The bottom action row: a divider, then the buttons side by side on one line that never wraps. */
export function PaneActions({ children, className }: { children?: ReactNode; className?: string }) {
  // Only PaneAction may sit in the row, so a text link or a differently sized button cannot creep in beside them:
  // development and tests fail loudly, a release drops the stranger rather than the whole pane.
  const all = flatten(children);
  // A marker rather than identity, so a hot-reloaded PaneAction still counts; keys are made unique across fragments.
  const items = all.filter(
    (item) => isValidElement(item) && (item.type as { paneAction?: boolean }).paneAction === true,
  );
  if (import.meta.env.DEV && items.length !== all.length)
    throw new Error("PaneActions holds only PaneAction children (design/README.md)");
  // A row whose every action is conditionally hidden draws no stray divider.
  if (!items.length) return null;
  return (
    // A fieldset is a named group: assistive technology announces the row as the pane's actions.
    <fieldset
      data-pane-actions=""
      aria-label={t("pane.actions")}
      className={cn(
        "mt-2 flex min-w-0 flex-nowrap items-center gap-1.5 border-t border-border px-1.5 pt-2 pb-1",
        className,
      )}
    >
      {items}
    </fieldset>
  );
}

/** The row's actions with fragments unwrapped and empty conditionals dropped; keys carry their fragment's key. */
function flatten(children: ReactNode, prefix = ""): ReactNode[] {
  return Children.toArray(children).flatMap((child) => {
    if (!isValidElement<{ children?: ReactNode }>(child)) return [child];
    if (child.type === Fragment) return flatten(child.props.children, `${prefix}${child.key}/`);
    return [prefix ? cloneElement(child, { key: `${prefix}${child.key}` }) : child];
  });
}

type Target = { onClick?: () => void; to?: string; href?: string };

/**
 * An action in the bottom row (design/README.md, "Pane actions"): icon-only by default, its words the accessible
 * name and the tooltip, so `children` is a plain string. `withText` shows the words beside the icon and says why
 * (an icon alone would be ambiguous, or the action is consequential); pane-actions-standards.test.ts lists them.
 * Outlined unless it is the pane's one primary action. `to` navigates inside the app, `href` opens outside it.
 */
export function PaneAction({
  icon: Icon,
  children,
  primary = false,
  destructive = false,
  to,
  href,
  withText,
  className,
  ...props
}: Omit<ComponentProps<"button">, "children"> &
  Target & { icon: LucideIcon; children: string; withText?: string; primary?: boolean; destructive?: boolean }) {
  const look = {
    size: "sm" as const,
    variant: primary ? ("default" as const) : ("outline" as const),
    className: cn(
      "shrink-0 pointer-coarse:h-11",
      !withText && "w-8 px-0 pointer-coarse:w-11",
      destructive && "text-bad",
      className,
    ),
    "data-pane-action": withText ? "text" : "icon",
    ...(withText ? {} : { "aria-label": children, title: children }),
  };
  const body = (
    <>
      <Icon aria-hidden />
      {withText && children}
    </>
  );
  if (to)
    return (
      <Button {...look} asChild>
        <Link to={to}>{body}</Link>
      </Button>
    );
  if (href)
    return (
      <Button {...look} asChild>
        <a href={href} target="_blank" rel="noopener noreferrer">
          {body}
        </a>
      </Button>
    );
  return (
    <Button type="button" {...look} {...props}>
      {body}
    </Button>
  );
}

PaneAction.paneAction = true;

/** A pane's title row: words only; every action is in the bottom row (design/README.md, "Pane actions"). */
export function PaneTitle({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-8 items-center px-1.5">
      <p className="min-w-0 font-medium">{children}</p>
    </div>
  );
}
