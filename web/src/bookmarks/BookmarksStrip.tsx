// The bookmarks bar under the browser's address row: the top-level bookmarks, one click each.
import { Button } from "@/components/ui/button";
import { t } from "@/i18n";
import { Favicon } from "./Favicon";
import { openBookmark } from "./open";
import { useBookmarks } from "./queries";
import { topLevel } from "./tree";

/** The person's bookmarks as a row of small buttons; nothing while there are none. */
export function BookmarksStrip() {
  const bookmarks = useBookmarks();
  const top = topLevel(bookmarks.data);
  if (!top.length) return null;
  return (
    <ul aria-label={t("bookmarks.title")} className="flex min-h-8 shrink-0 items-center gap-1 overflow-x-auto px-2">
      {top.map((entry) => (
        <li key={entry.id} className="shrink-0">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-7 max-w-40 gap-1 px-2 text-xs"
            title={entry.url ?? entry.title}
            onClick={() => openBookmark(entry.url)}
          >
            <Favicon image={entry.origin ? bookmarks.data?.favicons?.[entry.origin] : null} className="size-3" />
            <span className="truncate">{entry.title}</span>
          </Button>
        </li>
      ))}
    </ul>
  );
}
