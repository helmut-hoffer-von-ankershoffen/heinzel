// The two places a pane's actions may live (design/README.md, "Actions in panes"): one icon-only button in the
// title row, or one bottom action row under a divider. Panes use these instead of hand-rolled rows or text links, so
// placement, divider, size and accessible names stay the same everywhere.
import type { LucideIcon } from "lucide-react";
import { Children, type ComponentProps, cloneElement, Fragment, isValidElement, type ReactNode } from "react";
import { Link } from "react-router";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n";
import { cn } from "@/lib/utils";

/** The bottom action row: a divider, then the buttons side by side, wrapping when the pane is narrow. */
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
        "mt-2 flex min-w-0 flex-wrap items-center gap-1.5 border-t border-border px-1.5 pt-2 pb-1",
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
 * An action in the bottom row: icon and text, outlined unless it is the pane's one primary action.
 * `to` navigates inside the app, `href` opens outside it in a new tab (with its external-link meaning in the icon).
 * `iconOnly` keeps a well-known verb (refresh, copy) to its icon at the same height; its text becomes the
 * accessible name and tooltip, so it must be a plain string.
 */
export function PaneAction({
  icon: Icon,
  children,
  primary = false,
  destructive = false,
  to,
  href,
  iconOnly = false,
  className,
  ...props
}: Omit<ComponentProps<"button">, "children"> &
  Target &
  (
    | { iconOnly?: false; icon?: LucideIcon; children: ReactNode }
    | { iconOnly: true; icon: LucideIcon; children: string }
  ) & { primary?: boolean; destructive?: boolean }) {
  const look = {
    size: "sm" as const,
    variant: primary ? ("default" as const) : ("outline" as const),
    className: cn(
      "pointer-coarse:h-11",
      iconOnly && "w-8 px-0 pointer-coarse:w-11",
      destructive && "text-bad",
      className,
    ),
    ...(iconOnly ? { "aria-label": children as string, title: children as string } : {}),
  };
  const body = (
    <>
      {Icon && <Icon aria-hidden />}
      {!iconOnly && children}
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

/** The one icon-only action in a pane's title row. `label` is its accessible name and tooltip: verb and object. */
export function PaneIconAction({
  icon: Icon,
  label,
  to,
  href,
  onClick,
  disabled,
  className,
}: Target & { icon: LucideIcon; label: string; disabled?: boolean; className?: string }) {
  const look = {
    variant: "ghost" as const,
    size: "icon" as const,
    className: cn("size-8 shrink-0 pointer-coarse:size-11", className),
    title: label,
    "aria-label": label,
  };
  const icon = <Icon aria-hidden />;
  if (to)
    return (
      <Button {...look} asChild>
        <Link to={to}>{icon}</Link>
      </Button>
    );
  if (href)
    return (
      <Button {...look} asChild>
        <a href={href} target="_blank" rel="noopener noreferrer">
          {icon}
        </a>
      </Button>
    );
  return (
    <Button type="button" {...look} disabled={disabled} onClick={onClick}>
      {icon}
    </Button>
  );
}

/** A pane title with its optional icon action at the right, for panes that draw their own heading. */
export function PaneTitle({ children, action }: { children: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex min-h-8 items-center justify-between gap-2 px-1.5">
      <p className="min-w-0 font-medium">{children}</p>
      {action}
    </div>
  );
}
