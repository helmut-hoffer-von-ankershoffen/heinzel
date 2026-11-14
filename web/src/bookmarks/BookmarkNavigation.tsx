// The navigation's bookmarks: the top-level bookmarks (not folders or their contents), each with its favicon.
import { Link } from "react-router";
import { t } from "@/i18n";
import { cn } from "@/lib/utils";
import { Favicon } from "./Favicon";
import { openInApp, opensInApp } from "./open";
import { useBookmarks } from "./queries";
import { topLevel } from "./tree";

type Props = Readonly<{
  compact?: boolean;
  /** The navigation's own link styles, so a bookmark looks like its neighbours. */
  linkClass: string;
}>;

export function BookmarkNavigation({ compact = false, linkClass }: Props) {
  const bookmarks = useBookmarks();
  const entries = topLevel(bookmarks.data);
  if (!entries.length) return null;
  const inApp = opensInApp();
  return (
    <section aria-label={t("bookmarks.title")}>
      {!compact && (
        <Link
          to="/bookmarks"
          className="mt-5 mb-1.5 flex items-center rounded-md pr-3 pl-2.5 text-ui-sm text-muted-foreground hover:text-foreground"
          title={t("bookmarks.manage")}
        >
          {t("bookmarks.title")}
        </Link>
      )}
      <ul className={cn("flex gap-0.5", compact ? "mt-2 flex-col items-center" : "flex-col")}>
        {entries.map((entry) => (
          <li key={entry.id}>
            <a
              href={entry.url ?? "#"}
              target="_blank"
              rel="noopener noreferrer"
              className={linkClass}
              title={compact ? entry.title : (entry.url ?? undefined)}
              aria-label={compact ? entry.title : undefined}
              onClick={(event) => {
                if (!inApp || !entry.url) return;
                event.preventDefault();
                void openInApp(entry.url);
              }}
            >
              <Favicon
                image={entry.origin ? bookmarks.data?.favicons?.[entry.origin] : null}
                className="size-(--icon)"
              />
              {!compact && <span className="truncate">{entry.title}</span>}
            </a>
          </li>
        ))}
      </ul>
    </section>
  );
}
