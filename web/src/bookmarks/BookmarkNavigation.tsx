// The navigation's bookmarks: the whole tree. Folders open and close (remembered), every bookmark has its favicon.
import { ChevronRight, Folder } from "lucide-react";
import { type KeyboardEvent, useRef, useState } from "react";
import { useNavigate } from "react-router";
import type { SchemaBookmark } from "@/api/client";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n";
import { cn } from "@/lib/utils";
import { Favicon } from "./Favicon";
import { openBookmark } from "./open";
import { useBookmarks } from "./queries";
import { entries, shownEntries, type TreeKey, topLevel, treeStep } from "./tree";

type Props = Readonly<{
  compact?: boolean;
  /** The navigation's own link styles, so a bookmark looks like its neighbours. */
  linkClass: string;
}>;

const OPEN = "hz.bookmarks.open";
const KEYS: readonly string[] = ["ArrowDown", "ArrowUp", "ArrowLeft", "ArrowRight", "Home", "End"];

/** The folders open in the navigation, remembered in this browser; storage may be unavailable, which is fine. */
function useOpenFolders(): [Set<string>, (id: string, open: boolean) => void] {
  const [open, setOpen] = useState<Set<string>>(() => {
    try {
      const stored: unknown = JSON.parse(localStorage.getItem(OPEN) ?? "[]");
      return new Set(Array.isArray(stored) ? stored.filter((id): id is string => typeof id === "string") : []);
    } catch {
      return new Set();
    }
  });
  const set = (id: string, value: boolean) =>
    setOpen((current) => {
      const next = new Set(current);
      if (value) next.add(id);
      else next.delete(id);
      try {
        localStorage.setItem(OPEN, JSON.stringify([...next]));
      } catch {
        // Not remembered then.
      }
      return next;
    });
  return [open, set];
}

export function BookmarkNavigation({ compact = false, linkClass }: Props) {
  const bookmarks = useBookmarks();
  const [open, setFolder] = useOpenFolders();
  const [focused, setFocused] = useState<string | null>(null);
  const tree = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();
  const all = entries(bookmarks.data);
  if (!all.length) return null;
  const icon = (entry: SchemaBookmark) => (
    <Favicon image={entry.origin ? bookmarks.data?.favicons?.[entry.origin] : null} className="size-(--icon)" />
  );
  // The collapsed rail has no room for a tree: it keeps the top-level bookmarks as icons.
  if (compact) {
    const top = topLevel(bookmarks.data);
    if (!top.length) return null;
    return (
      <ul aria-label={t("bookmarks.title")} className="mt-2 flex flex-col items-center gap-0.5">
        {top.map((entry) => (
          <li key={entry.id}>
            <Button
              type="button"
              variant="ghost"
              className={cn(linkClass, "h-auto px-[inherit]")}
              title={entry.title}
              aria-label={entry.title}
              onClick={() => openBookmark(entry.url)}
            >
              {icon(entry)}
            </Button>
          </li>
        ))}
      </ul>
    );
  }
  const shown = shownEntries(bookmarks.data, open);
  // One item of the tree is in the tab order: the one last focused, else the first.
  const tabStop = shown.some((entry) => entry.id === focused) ? focused : shown[0]?.id;
  // A step either moves or folds, never both: the item to focus is drawn already.
  const focus = (id: string) => {
    setFocused(id);
    tree.current?.querySelector<HTMLElement>(`[data-bookmark="${id}"]`)?.focus();
  };
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const current = (event.target as HTMLElement).dataset?.bookmark;
    if (!current || !KEYS.includes(event.key) || event.altKey || event.ctrlKey || event.metaKey) return;
    event.preventDefault();
    const step = treeStep(shown, open, current, event.key as TreeKey);
    if (step.open) setFolder(step.open, true);
    if (step.close) setFolder(step.close, false);
    if (step.focus) focus(step.focus);
  };
  return (
    <div>
      <Button
        type="button"
        variant="ghost"
        onClick={() => navigate("/bookmarks")}
        className="mt-5 mb-1.5 h-auto justify-start rounded-md pr-3 pl-2.5 text-ui-sm font-normal text-muted-foreground hover:bg-transparent hover:text-foreground"
        title={t("bookmarks.manage")}
      >
        {t("bookmarks.title")}
      </Button>
      {/* A long tree scrolls here, inside its section, and leaves the rest of the navigation where it is. */}
      <div
        ref={tree}
        role="tree"
        aria-label={t("bookmarks.title")}
        onKeyDown={onKeyDown}
        className="flex max-h-64 flex-col gap-0.5 overflow-y-auto"
      >
        {shown.map((entry) => {
          const depth = entry.depth ?? 0;
          const shared = {
            role: "treeitem",
            "aria-level": depth + 1,
            "data-bookmark": entry.id,
            tabIndex: entry.id === tabStop ? 0 : -1,
            onFocus: () => setFocused(entry.id),
            className: cn(linkClass, "h-auto w-full justify-start text-left font-normal"),
            style: { paddingLeft: `${0.625 + depth * 0.875}rem` },
          } as const;
          return entry.kind === "folder" ? (
            <Button
              key={entry.id}
              type="button"
              variant="ghost"
              {...shared}
              aria-expanded={open.has(entry.id)}
              onClick={() => setFolder(entry.id, !open.has(entry.id))}
            >
              <ChevronRight aria-hidden className={cn("transition-transform", open.has(entry.id) && "rotate-90")} />
              <Folder aria-hidden />
              <span className="truncate">{entry.title}</span>
            </Button>
          ) : (
            <Button
              key={entry.id}
              type="button"
              variant="ghost"
              {...shared}
              title={entry.url ?? undefined}
              onClick={() => openBookmark(entry.url)}
            >
              {icon(entry)}
              <span className="truncate">{entry.title}</span>
            </Button>
          );
        })}
      </div>
    </div>
  );
}
