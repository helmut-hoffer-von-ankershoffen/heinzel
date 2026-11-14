// The star in the browser's address bar: add the page, then name it, pick its folder or remove it.
import { Star } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useNavigate } from "react-router";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n";
import { bookmarkCalls, useBookmarkCall, useBookmarks } from "./queries";
import { bookmarkOf, entries, folders, originOf } from "./tree";

type Tab = { url: string; favicon?: string | null };
type Props = Readonly<{
  /** Where the star button goes: an element inside the address form. */
  slot: HTMLElement | null;
  /** The shown page and its title. */
  url: string | undefined;
  title: string;
  /** Every open tab: the icon of a bookmarked site is handed to hz once, per origin. */
  tabs: readonly Tab[];
}>;

/**
 * The star button (drawn into `slot`) and, while open, the editor line for the page at `url`. Two places, because
 * the button sits inside the address form and the editor has fields of its own below the tab strip.
 */
export function BookmarkBar({ slot, url, title, tabs }: Props) {
  const bookmarks = useBookmarks();
  const navigate = useNavigate();
  const add = useBookmarkCall(bookmarkCalls.add);
  const edit = useBookmarkCall(bookmarkCalls.edit);
  const move = useBookmarkCall(bookmarkCalls.move);
  const remove = useBookmarkCall(bookmarkCalls.remove);
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const nameId = useId();
  const folderId = useId();
  const found = bookmarkOf(bookmarks.data, url);
  const canStar = originOf(url) !== null;
  const starred = Boolean(found);
  const foundId = found?.id;
  const foundTitle = found?.title;
  // Another page, or a bookmark changed elsewhere: the editor shows that one's name; no bookmark closes it.
  useEffect(() => {
    if (foundId === undefined) setOpen(false);
    else setName(foundTitle ?? "");
  }, [foundId, foundTitle]);
  // hz keeps the icon a bookmarked site itself showed, per origin; nothing asks a favicon service.
  const keepIcon = useBookmarkCall(bookmarkCalls.favicon);
  const handed = useRef(new Map<string, string>());
  const known = bookmarks.data;
  useEffect(() => {
    if (!known) return;
    const origins = new Set(entries(known).map((entry) => entry.origin));
    for (const tab of tabs) {
      const origin = originOf(tab.url);
      if (!origin || !tab.favicon || !origins.has(origin)) continue;
      if (known.favicons?.[origin] === tab.favicon || handed.current.get(origin) === tab.favicon) continue;
      handed.current.set(origin, tab.favicon);
      keepIcon.mutate({ origin, image: tab.favicon });
    }
  }, [known, tabs, keepIcon.mutate]);
  const failure = add.error ?? edit.error ?? move.error ?? remove.error;
  const saveName = () => {
    if (found && name.trim() && name.trim() !== found.title) edit.mutate({ entry: found.id, title: name.trim() });
  };
  const label = found ? t("bookmarks.starEdit") : t("bookmarks.starAdd");
  const button = (
    <Button
      type="button"
      variant="ghost"
      size="icon"
      aria-label={label}
      title={label}
      aria-pressed={starred}
      aria-expanded={open}
      disabled={!canStar || add.isPending}
      onClick={() => {
        if (found) setOpen((shown) => !shown);
        else if (url) add.mutate({ url, title }, { onSuccess: () => setOpen(true) });
      }}
    >
      <Star aria-hidden className={found ? "fill-current" : undefined} />
    </Button>
  );
  const editor =
    open && found ? (
      <form
        aria-label={t("bookmarks.editTitle")}
        className="flex shrink-0 flex-wrap items-center gap-2 border-t-[0.5px] border-foreground/20 px-3 py-1.5 text-xs"
        onSubmit={(event) => {
          event.preventDefault();
          saveName();
          setOpen(false);
        }}
      >
        <label htmlFor={nameId}>{t("bookmarks.name")}</label>
        <input
          id={nameId}
          type="text"
          value={name}
          maxLength={200}
          onChange={(event) => setName(event.target.value)}
          onBlur={saveName}
          className="min-w-24 flex-1 rounded-lg border border-border bg-background px-2 py-1"
        />
        <label htmlFor={folderId}>{t("bookmarks.folder")}</label>
        <select
          id={folderId}
          value={found.parent ?? ""}
          onChange={(event) => move.mutate({ entry: found.id, parent: event.target.value || null, before: null })}
          className="max-w-40 rounded-lg border border-border bg-background px-2 py-1"
        >
          <option value="">{t("bookmarks.topLevel")}</option>
          {folders(bookmarks.data).map((folder) => (
            <option key={folder.id} value={folder.id}>
              {`${"  ".repeat(folder.depth ?? 0)}${folder.title}`}
            </option>
          ))}
        </select>
        <Button type="button" variant="ghost" size="sm" onClick={() => remove.mutate(found.id)}>
          {t("bookmarks.remove")}
        </Button>
        <Button type="button" variant="ghost" size="sm" onClick={() => navigate("/bookmarks")}>
          {t("bookmarks.manage")}
        </Button>
        <Button type="submit" variant="ghost" size="sm">
          {t("bookmarks.done")}
        </Button>
      </form>
    ) : null;
  return (
    <>
      {slot && createPortal(button, slot)}
      {editor}
      {failure && (
        <p role="alert" className="shrink-0 border-t-[0.5px] border-foreground/20 px-3 py-1.5 text-xs">
          {failure.message || t("bookmarks.failed")}
        </p>
      )}
    </>
  );
}
