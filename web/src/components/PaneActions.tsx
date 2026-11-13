// Actions (design/README.md, "Actions"): chrome as at most three ghost icons at the right of the title row, content
// actions in one bottom row under a divider. The first action leads with icon and words, every other one is
// icon-only (its words are its name and tooltip), destructive last; a lead the row has no room for takes its own line.
import type { LucideIcon } from "lucide-react";
import {
  Children,
  type ComponentProps,
  cloneElement,
  createContext,
  Fragment,
  isValidElement,
  type ReactNode,
  useContext,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { Link } from "react-router";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n";
import { cn } from "@/lib/utils";

/** A footer pane's way to close itself: an action that takes the person elsewhere calls it, so the pane never covers
 *  what it opened. Outside a pane there is nothing to close. */
export const PaneLeave = createContext<(() => void) | null>(null);

type Words = "lead" | "wrap" | "all" | "none";
/** How the lead fits its row: beside the icons, on a line of its own above them, or (alone too wide) as an icon. */
type Fit = "line" | "wrap" | "icon";
const MODE: Record<Fit, Words> = { line: "lead", wrap: "wrap", icon: "none" };
/** What the row tells each action: whether it shows its words. Outside a row (chrome) every action is an icon. */
const Slot = createContext<{ index: number; words: Words }>({ index: 1, words: "lead" });

/**
 * The only action row: a divider, then the actions.
 * `words="lead"` (default): the first action shows its words, the rest are icons on one line. When the row is too
 * narrow for all of them, the lead takes a full-width line of its own above the icons (`data-words="wrap"`), so its
 * words never collapse; only a lead too wide even alone drops to its icon (`data-words="none"`). Measured before
 * paint and again on every resize: a row that grows gets its one line back.
 * `words="all"` with `align="end"`: a dialog's answers, Cancel first and the committing action last.
 */
export function PaneActions({
  children,
  className,
  align = "start",
  words = "lead",
  frame = false,
}: {
  children?: ReactNode;
  className?: string;
  align?: "start" | "end";
  words?: "lead" | "all";
  /** The row a footer pane's frame draws (FooterItem); a row drawn inside a pane's content is not. */
  frame?: boolean;
}) {
  // Only PaneAction may sit in the row, so a text link or a differently sized button cannot creep in beside them:
  // development and tests fail loudly, a release drops the stranger rather than the whole pane.
  const all = flatten(children);
  // A marker rather than identity, so a hot-reloaded PaneAction still counts; keys are made unique across fragments.
  const items = all.filter(
    (item) => isValidElement(item) && (item.type as { paneAction?: boolean }).paneAction === true,
  );
  if (import.meta.env.DEV && items.length !== all.length)
    throw new Error("PaneActions holds only PaneAction children (design/README.md)");
  const row = useRef<HTMLFieldSetElement>(null);
  const [fit, setFit] = useState<Fit>("line");
  // The row width each roomier fit needs, learnt when it overflowed: one line, and the lead on a line of its own.
  const needs = useRef({ line: 0, lead: 0 });
  // The row's shape: every action's key and the lead's words. Any change makes the learnt widths stale.
  const shape = items
    .map((item, index) => (isValidElement<{ children?: unknown }>(item) ? `${item.key}` : `${index}`))
    .concat(isValidElement<{ children?: unknown }>(items[0]) ? String(items[0].props.children) : "")
    .join("\n");
  const measured = useRef(shape);
  useLayoutEffect(() => {
    const element = row.current;
    if (words !== "lead" || !element) return;
    if (measured.current !== shape) {
      // A new shape forgets what it learnt and measures from one line; drawn in another fit, it first returns to
      // one line, and the next layout effect measures that.
      measured.current = shape;
      needs.current = { line: 0, lead: 0 };
      if (fit !== "line") {
        setFit("line");
        return;
      }
    }
    const measure = () => setFit(nextFit(element, fit, needs.current));
    measure();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    observer?.observe(element);
    return () => observer?.disconnect();
  });
  // A row whose every action is conditionally hidden draws no stray divider.
  if (!items.length) return null;
  const mode: Words = words === "all" ? "all" : MODE[fit];
  return (
    // A fieldset is a named group: assistive technology announces the row as the pane's actions.
    <fieldset
      ref={row}
      data-pane-actions=""
      data-pane-frame={frame ? "" : undefined}
      data-words={mode}
      aria-label={t("pane.actions")}
      className={cn(
        "mt-2 flex min-w-0 items-center gap-1.5 border-t border-border px-1.5 pt-2 pb-1",
        mode === "wrap" ? "flex-wrap" : "flex-nowrap",
        align === "end" && "justify-end",
        className,
      )}
    >
      {items.map((item, index) => (
        <Slot.Provider key={isValidElement(item) ? item.key : index} value={{ index, words: mode }}>
          {item}
        </Slot.Provider>
      ))}
    </fieldset>
  );
}

/**
 * The fit a row's current layout calls for. Overflow steps down (one line, then the lead on its own line, then the
 * lead's icon) and records the row width the roomier fit needed; a row at least that wide steps back up.
 */
function nextFit(element: HTMLElement, fit: Fit, needs: { line: number; lead: number }): Fit {
  const width = element.clientWidth;
  if (fit === "line") {
    if (element.scrollWidth <= width + 1) return "line";
    needs.line = element.scrollWidth;
    return "wrap";
  }
  if (needs.line && width >= needs.line) return "line";
  if (fit === "icon") return needs.lead && width >= needs.lead ? "wrap" : "icon";
  const first = element.querySelector<HTMLElement>("[data-pane-action]");
  if (!first || first.scrollWidth <= first.clientWidth + 1) return "wrap";
  needs.lead = width + first.scrollWidth - first.clientWidth;
  return "icon";
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
 * One action. `icon` is required and `children` are its words: shown beside the icon when the row says so (the
 * lead, or every action of a dialog's row), otherwise the accessible name and the tooltip. Outlined; `primary` is
 * the container's one filled action, `destructive` is red (and last, behind a confirming dialog), `ghost` is chrome
 * or a list row's action. `to` navigates inside the app, `href` opens outside it in a new tab.
 */
export function PaneAction({
  icon: Icon,
  children,
  primary = false,
  destructive = false,
  ghost = false,
  leaves = false,
  to,
  href,
  className,
  ...props
}: Omit<ComponentProps<"button">, "children"> &
  Target & {
    icon: LucideIcon;
    children: string;
    primary?: boolean;
    destructive?: boolean;
    ghost?: boolean;
    leaves?: boolean;
  }) {
  const slot = useContext(Slot);
  const leave = useContext(PaneLeave);
  // Going somewhere in the app (`to=`, or `leaves`) closes the pane the action sits in.
  if (leave && (to || leaves)) {
    const own = props as { onClick?: (event: unknown) => void };
    const click = own.onClick;
    own.onClick = (event) => {
      click?.(event);
      leave();
    };
  }
  const words = slot.words === "all" || ((slot.words === "lead" || slot.words === "wrap") && slot.index === 0);
  // The lead of a wrapped row fills its own line. `whitespace-nowrap` keeps its words on one line, so a lead too
  // wide even alone overflows measurably and steps to its icon instead of breaking its words onto a second line.
  const alone = slot.words === "wrap" && slot.index === 0;
  const look = {
    size: "sm" as const,
    variant: primary ? ("default" as const) : ghost ? ("ghost" as const) : ("outline" as const),
    className: cn(
      "shrink-0 pointer-coarse:h-11",
      !words && "w-8 px-0 pointer-coarse:w-11",
      alone && "basis-full whitespace-nowrap",
      // Red: the outlined icon in place, or the filled committing button of a dialog that destroys something.
      destructive && (primary ? "bg-bad text-white" : "text-bad"),
      className,
    ),
    "data-pane-action": words ? "text" : "icon",
    ...(words ? {} : { "aria-label": children, title: children }),
  };
  const body = (
    <>
      <Icon aria-hidden />
      {words && children}
    </>
  );
  if (to)
    return (
      <Button {...look} asChild>
        <Link to={to} {...(props as Omit<ComponentProps<"a">, "href">)}>
          {body}
        </Link>
      </Button>
    );
  if (href)
    return (
      <Button {...look} asChild>
        <a
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          onClick={props.onClick as ComponentProps<"a">["onClick"]}
        >
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

/**
 * A container's title row: its words, then `chrome`, at most three ghost icon `PaneAction`s acting on the container
 * itself (Close, Dismiss, Collapse). Footer panes have no chrome: they close on their own.
 */
export function PaneTitle({
  children,
  chrome,
  as: Heading = "p",
}: {
  children: ReactNode;
  chrome?: ReactNode;
  /** The element for the words: a panel's title is a heading. */
  as?: "p" | "h2" | "h3";
}) {
  return (
    <div className="flex min-h-8 min-w-0 flex-1 items-center justify-between gap-2 px-1.5">
      <Heading className="min-w-0 font-medium">{children}</Heading>
      {chrome && (
        <div data-pane-chrome="" className="flex shrink-0 items-center gap-1.5">
          {chrome}
        </div>
      )}
    </div>
  );
}

/**
 * Chrome for a container without a title row of its own (a card whose heading is part of its body): the same ghost
 * icons, pinned to the top right corner. The body leaves `pr-10` (one icon) or `pr-20` (two) free.
 */
export function PaneChrome({ children }: { children: ReactNode }) {
  return (
    <div data-pane-chrome="" className="absolute top-1.5 right-1.5 flex items-center gap-1.5">
      {children}
    </div>
  );
}

/** Buttons and links a container may show only in its action row or chrome; switches, the radios of a segmented choice and form fields are content. */
const ACTION =
  'button:not([role="switch"]):not([role="radio"]), a[href]:not([data-prose-link]):not([data-nav-row]), [role="button"], [role="link"]:not([data-prose-link]):not([data-nav-row]), [role="menuitem"]';

/**
 * What breaks the action structure (design/README.md, "Actions") of a pane, card or dialog: an action outside the
 * row or chrome, content after the row, a second row, an action that is not a PaneAction, one without an icon or a
 * name, words on more than the lead (unless the row is a dialog's), and actions on more than one line.
 */
export function actionStructureProblems(container: Element): string[] {
  const rows = container.querySelectorAll("[data-pane-actions]");
  const problems: string[] = [];
  if (rows.length > 1) problems.push(`${rows.length} action rows; a pane has one`);
  const row = rows[0];
  const chromes = [...container.querySelectorAll("[data-pane-chrome]")];
  for (const element of container.querySelectorAll(ACTION))
    if (!row?.contains(element) && !chromes.some((chrome) => chrome.contains(element)))
      problems.push(`"${element.getAttribute("aria-label") ?? element.textContent?.trim()}" is outside the action row`);
  const all = container.querySelectorAll("*");
  const last = all[all.length - 1];
  if (row && last && !row.contains(last)) problems.push("content follows the action row");
  if (!row) return problems;
  const actions = [...row.querySelectorAll(ACTION)];
  const name = (element: Element) => element.getAttribute("aria-label") ?? element.textContent?.trim();
  const mode = row.getAttribute("data-words");
  for (const [index, action] of actions.entries()) {
    if (!action.hasAttribute("data-pane-action")) {
      problems.push(`"${name(action)}" is not a PaneAction`);
      continue;
    }
    if (!action.querySelector("svg")) problems.push(`"${name(action)}" has no icon`);
    const words = action.getAttribute("data-pane-action") === "text";
    if (!words && !(action.getAttribute("aria-label") && action.getAttribute("title")))
      problems.push(`"${name(action)}" is icon-only without a name and tooltip`);
    const expected = mode === "all" || ((mode === "lead" || mode === "wrap") && index === 0);
    if (words !== expected)
      problems.push(`"${name(action)}" ${words ? "shows words; only the lead does" : "is the lead without its words"}`);
  }
  // One line (measurable wherever layout runs); a wrapped row's lead alone sits above it.
  const top = (action: Element) => Math.round(action.getBoundingClientRect().top);
  const line = mode === "wrap" ? actions.slice(1) : actions;
  const tops = new Set(line.map(top));
  if (tops.size > 1) problems.push(`the actions wrap onto ${tops.size} lines`);
  const [first] = actions;
  if (mode === "wrap" && first && line.some((action) => top(action) <= top(first)))
    problems.push("the wrapped lead is not on a line of its own above the other actions");
  return problems;
}

/** Throws, in development and tests only, when a container breaks the action structure. */
export function assertActionStructure(container: Element) {
  if (!import.meta.env.DEV) return;
  const problems = actionStructureProblems(container);
  if (problems.length) throw new Error(`Action structure (design/README.md, "Actions"): ${problems.join("; ")}`);
}
